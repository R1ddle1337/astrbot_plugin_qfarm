/**
 * 好友农场操作 - 进入/离开/帮忙/偷菜/巡查
 */

const { CONFIG, PlantPhase, PHASE_NAMES } = require('./config');
const { types } = require('./proto');
const { sendMsgAsync, getUserState, networkEvents } = require('./network');
const { toLong, toNum, getServerTimeSec, log, logWarn, sleep } = require('./utils');
const { getCurrentPhase, setOperationLimitsCallback } = require('./farm');
const { recordOperation } = require('./stats');
const { isAutomationOn, getFriendQuietHours } = require('./store');
const { getPlantName, getPlantById, getSeedImageBySeedId } = require('./gameConfig');
const { sellAllFruits } = require('./warehouse');

// ============ 内部状态 ============
let isCheckingFriends = false;
let isFirstFriendCheck = true;
let friendCheckTimer = null;
let friendLoopRunning = false;
let externalSchedulerMode = false;
let lastResetDate = '';  // 上次重置日期 (YYYY-MM-DD)

// 操作限制状态 (从服务器响应中更新)
// 操作类型ID (根据游戏代码):
// 10001 = 收获, 10002 = 铲除, 10003 = 放草, 10004 = 放虫
// 10005 = 除草(帮好友), 10006 = 除虫(帮好友), 10007 = 浇水(帮好友), 10008 = 偷菜
const operationLimits = new Map();

// 操作类型名称映射
const OP_NAMES = {
    10001: '收获',
    10002: '铲除',
    10003: '放草',
    10004: '放虫',
    10005: '除草',
    10006: '除虫',
    10007: '浇水',
    10008: '偷菜',
};

// 配置: 是否只在有经验时才帮助好友
const HELP_ONLY_WITH_EXP = true;

function parseTimeToMinutes(timeStr) {
    const m = String(timeStr || '').match(/^(\d{1,2}):(\d{1,2})$/);
    if (!m) return null;
    const h = parseInt(m[1], 10);
    const min = parseInt(m[2], 10);
    if (Number.isNaN(h) || Number.isNaN(min) || h < 0 || h > 23 || min < 0 || min > 59) return null;
    return h * 60 + min;
}

function inFriendQuietHours(now = new Date()) {
    const cfg = getFriendQuietHours();
    if (!cfg || !cfg.enabled) return false;

    const start = parseTimeToMinutes(cfg.start);
    const end = parseTimeToMinutes(cfg.end);
    if (start === null || end === null) return false;

    const cur = now.getHours() * 60 + now.getMinutes();
    if (start === end) return true; // 起止相同视为全天静默
    if (start < end) return cur >= start && cur < end;
    return cur >= start || cur < end; // 跨天时段
}

// ============ 好友 API ============

async function getAllFriends() {
    const body = types.GetAllFriendsRequest.encode(types.GetAllFriendsRequest.create({})).finish();
    const { body: replyBody } = await sendMsgAsync('gamepb.friendpb.FriendService', 'GetAll', body);
    return types.GetAllFriendsReply.decode(replyBody);
}

// ============ 好友申请 API (微信同玩) ============

async function getApplications() {
    const body = types.GetApplicationsRequest.encode(types.GetApplicationsRequest.create({})).finish();
    const { body: replyBody } = await sendMsgAsync('gamepb.friendpb.FriendService', 'GetApplications', body);
    return types.GetApplicationsReply.decode(replyBody);
}

async function acceptFriends(gids) {
    const body = types.AcceptFriendsRequest.encode(types.AcceptFriendsRequest.create({
        friend_gids: gids.map(g => toLong(g)),
    })).finish();
    const { body: replyBody } = await sendMsgAsync('gamepb.friendpb.FriendService', 'AcceptFriends', body);
    return types.AcceptFriendsReply.decode(replyBody);
}

async function enterFriendFarm(friendGid) {
    const body = types.VisitEnterRequest.encode(types.VisitEnterRequest.create({
        host_gid: toLong(friendGid),
        reason: 2,  // ENTER_REASON_FRIEND
    })).finish();
    const { body: replyBody } = await sendMsgAsync('gamepb.visitpb.VisitService', 'Enter', body);
    return types.VisitEnterReply.decode(replyBody);
}

async function leaveFriendFarm(friendGid) {
    const body = types.VisitLeaveRequest.encode(types.VisitLeaveRequest.create({
        host_gid: toLong(friendGid),
    })).finish();
    try {
        await sendMsgAsync('gamepb.visitpb.VisitService', 'Leave', body);
    } catch (e) { /* 离开失败不影响主流程 */ }
}

/**
 * 检查是否需要重置每日限制 (0点刷新)
 */
function checkDailyReset() {
    const today = new Date().toISOString().slice(0, 10);  // YYYY-MM-DD
    if (lastResetDate !== today) {
        if (lastResetDate !== '') {
            log('系统', '跨日重置，清空操作限制缓存');
        }
        operationLimits.clear();
        lastResetDate = today;
    }
}

/**
 * 更新操作限制状态
 */
function updateOperationLimits(limits) {
    if (!limits || limits.length === 0) return;
    checkDailyReset();
    for (const limit of limits) {
        const id = toNum(limit.id);
        if (id > 0) {
            const data = {
                dayTimes: toNum(limit.day_times),
                dayTimesLimit: toNum(limit.day_times_lt),
                dayExpTimes: toNum(limit.day_exp_times),
                dayExpTimesLimit: toNum(limit.day_ex_times_lt),  // 注意: 字段名是 day_ex_times_lt (少个p)
            };
            operationLimits.set(id, data);
        }
    }
}

/**
 * 检查某操作是否还能获得经验
 */
function canGetExp(opId) {
    const limit = operationLimits.get(opId);
    if (!limit) return false;  // 没有限制信息，保守起见不帮助（等待农场检查获取限制）
    if (limit.dayExpTimesLimit <= 0) return true;  // 没有经验上限
    return limit.dayExpTimes < limit.dayExpTimesLimit;
}

/**
 * 检查某操作是否还有次数
 */
function canOperate(opId) {
    const limit = operationLimits.get(opId);
    if (!limit) return true;
    if (limit.dayTimesLimit <= 0) return true;
    return limit.dayTimes < limit.dayTimesLimit;
}

/**
 * 获取某操作剩余次数
 */
function getRemainingTimes(opId) {
    const limit = operationLimits.get(opId);
    if (!limit || limit.dayTimesLimit <= 0) return 999;
    return Math.max(0, limit.dayTimesLimit - limit.dayTimes);
}

/**
 * 获取操作限制详情 (供管理面板使用)
 */
function getOperationLimits() {
    const result = {};
    for (const id of [10001, 10002, 10003, 10004, 10005, 10006, 10007, 10008]) {
        const limit = operationLimits.get(id);
        if (limit) {
            result[id] = {
                name: OP_NAMES[id] || `#${id}`,
                ...limit,
                remaining: getRemainingTimes(id),
            };
        }
    }
    return result;
}

/**
 * 获取操作限制摘要 (用于日志显示)
 */
function getOperationLimitsSummary() {
    const parts = [];
    // 帮助好友操作 (10005=除草, 10006=除虫, 10007=浇水, 10008=偷菜)
    for (const id of [10005, 10006, 10007, 10008]) {
        const limit = operationLimits.get(id);
        if (limit && limit.dayExpTimesLimit > 0) {
            const name = OP_NAMES[id] || `#${id}`;
            const expLeft = limit.dayExpTimesLimit - limit.dayExpTimes;
            parts.push(`${name}${expLeft}/${limit.dayExpTimesLimit}`);
        }
    }
    // 捣乱操作 (10003=放草, 10004=放虫)
    for (const id of [10003, 10004]) {
        const limit = operationLimits.get(id);
        if (limit && limit.dayTimesLimit > 0) {
            const name = OP_NAMES[id] || `#${id}`;
            const left = limit.dayTimesLimit - limit.dayTimes;
            parts.push(`${name}${left}/${limit.dayTimesLimit}`);
        }
    }
    return parts;
}

async function helpWater(friendGid, landIds) {
    const body = types.WaterLandRequest.encode(types.WaterLandRequest.create({
        land_ids: landIds,
        host_gid: toLong(friendGid),
    })).finish();
    const { body: replyBody } = await sendMsgAsync('gamepb.plantpb.PlantService', 'WaterLand', body);
    const reply = types.WaterLandReply.decode(replyBody);
    updateOperationLimits(reply.operation_limits);
    return reply;
}

async function helpWeed(friendGid, landIds) {
    const body = types.WeedOutRequest.encode(types.WeedOutRequest.create({
        land_ids: landIds,
        host_gid: toLong(friendGid),
    })).finish();
    const { body: replyBody } = await sendMsgAsync('gamepb.plantpb.PlantService', 'WeedOut', body);
    const reply = types.WeedOutReply.decode(replyBody);
    updateOperationLimits(reply.operation_limits);
    return reply;
}

async function helpInsecticide(friendGid, landIds) {
    const body = types.InsecticideRequest.encode(types.InsecticideRequest.create({
        land_ids: landIds,
        host_gid: toLong(friendGid),
    })).finish();
    const { body: replyBody } = await sendMsgAsync('gamepb.plantpb.PlantService', 'Insecticide', body);
    const reply = types.InsecticideReply.decode(replyBody);
    updateOperationLimits(reply.operation_limits);
    return reply;
}

async function stealHarvest(friendGid, landIds) {
    const body = types.HarvestRequest.encode(types.HarvestRequest.create({
        land_ids: landIds,
        host_gid: toLong(friendGid),
        is_all: true,
    })).finish();
    const { body: replyBody } = await sendMsgAsync('gamepb.plantpb.PlantService', 'Harvest', body);
    const reply = types.HarvestReply.decode(replyBody);
    updateOperationLimits(reply.operation_limits);
    return reply;
}

async function putInsects(friendGid, landIds) {
    let ok = 0;
    const ids = Array.isArray(landIds) ? landIds : [];
    for (const landId of ids) {
        try {
            const body = types.PutInsectsRequest.encode(types.PutInsectsRequest.create({
                land_ids: [toLong(landId)],
                host_gid: toLong(friendGid),
            })).finish();
            const { body: replyBody } = await sendMsgAsync('gamepb.plantpb.PlantService', 'PutInsects', body);
            const reply = types.PutInsectsReply.decode(replyBody);
            updateOperationLimits(reply.operation_limits);
            ok++;
        } catch (e) { /* ignore single failure */ }
        await sleep(100);
    }
    return ok;
}

async function putWeeds(friendGid, landIds) {
    let ok = 0;
    const ids = Array.isArray(landIds) ? landIds : [];
    for (const landId of ids) {
        try {
            const body = types.PutWeedsRequest.encode(types.PutWeedsRequest.create({
                land_ids: [toLong(landId)],
                host_gid: toLong(friendGid),
            })).finish();
            const { body: replyBody } = await sendMsgAsync('gamepb.plantpb.PlantService', 'PutWeeds', body);
            const reply = types.PutWeedsReply.decode(replyBody);
            updateOperationLimits(reply.operation_limits);
            ok++;
        } catch (e) { /* ignore single failure */ }
        await sleep(100);
    }
    return ok;
}

async function putInsectsDetailed(friendGid, landIds) {
    let ok = 0;
    const failed = [];
    const ids = Array.isArray(landIds) ? landIds : [];
    for (const landId of ids) {
        try {
            const body = types.PutInsectsRequest.encode(types.PutInsectsRequest.create({
                land_ids: [toLong(landId)],
                host_gid: toLong(friendGid),
            })).finish();
            const { body: replyBody } = await sendMsgAsync('gamepb.plantpb.PlantService', 'PutInsects', body);
            const reply = types.PutInsectsReply.decode(replyBody);
            updateOperationLimits(reply.operation_limits);
            ok++;
        } catch (e) {
            failed.push({ landId, reason: e && e.message ? e.message : '未知错误' });
        }
        await sleep(100);
    }
    return { ok, failed };
}

async function putWeedsDetailed(friendGid, landIds) {
    let ok = 0;
    const failed = [];
    const ids = Array.isArray(landIds) ? landIds : [];
    for (const landId of ids) {
        try {
            const body = types.PutWeedsRequest.encode(types.PutWeedsRequest.create({
                land_ids: [toLong(landId)],
                host_gid: toLong(friendGid),
            })).finish();
            const { body: replyBody } = await sendMsgAsync('gamepb.plantpb.PlantService', 'PutWeeds', body);
            const reply = types.PutWeedsReply.decode(replyBody);
            updateOperationLimits(reply.operation_limits);
            ok++;
        } catch (e) {
            failed.push({ landId, reason: e && e.message ? e.message : '未知错误' });
        }
        await sleep(100);
    }
    return { ok, failed };
}

async function checkCanOperateRemote(friendGid, operationId) {
    if (!types.CheckCanOperateRequest || !types.CheckCanOperateReply) {
        return { canOperate: true, canStealNum: 0 };
    }
    try {
        const body = types.CheckCanOperateRequest.encode(types.CheckCanOperateRequest.create({
            host_gid: toLong(friendGid),
            operation_id: toLong(operationId),
        })).finish();
        const { body: replyBody } = await sendMsgAsync('gamepb.plantpb.PlantService', 'CheckCanOperate', body);
        const reply = types.CheckCanOperateReply.decode(replyBody);
        return {
            canOperate: !!reply.can_operate,
            canStealNum: toNum(reply.can_steal_num),
        };
    } catch {
        // 预检查失败时降级为不拦截，避免因协议抖动导致完全不操作
        return { canOperate: true, canStealNum: 0 };
    }
}

// ============ 好友土地分析 ============

// 调试开关 - 设为好友名字可只查看该好友的土地分析详情，设为 true 查看全部，false 关闭
const DEBUG_FRIEND_LANDS = false;

function analyzeFriendLands(lands, myGid, friendName = '') {
    const result = {
        stealable: [],   // 可偷
        stealableInfo: [],  // 可偷植物信息 { landId, plantId, name }
        needWater: [],   // 需要浇水
        needWeed: [],    // 需要除草
        needBug: [],     // 需要除虫
        canPutWeed: [],  // 可以放草
        canPutBug: [],   // 可以放虫
    };

    for (const land of lands) {
        const id = toNum(land.id);
        const plant = land.plant;
        // 是否显示此好友的调试信息
        const showDebug = DEBUG_FRIEND_LANDS === true || DEBUG_FRIEND_LANDS === friendName;

        if (!plant || !plant.phases || plant.phases.length === 0) {
            if (showDebug) console.log(`  [${friendName}] 土地#${id}: 无植物或无阶段数据`);
            continue;
        }

        const currentPhase = getCurrentPhase(plant.phases, showDebug, `[${friendName}]土地#${id}`);
        if (!currentPhase) {
            if (showDebug) console.log(`  [${friendName}] 土地#${id}: getCurrentPhase返回null`);
            continue;
        }
        const phaseVal = currentPhase.phase;

        if (showDebug) {
            const insectOwners = plant.insect_owners || [];
            const weedOwners = plant.weed_owners || [];
            console.log(`  [${friendName}] 土地#${id}: phase=${phaseVal} stealable=${plant.stealable} dry=${toNum(plant.dry_num)} weed=${weedOwners.length} bug=${insectOwners.length}`);
        }

        if (phaseVal === PlantPhase.MATURE) {
            if (plant.stealable) {
                result.stealable.push(id);
                const plantId = toNum(plant.id);
                const plantName = getPlantName(plantId) || plant.name || '未知';
                result.stealableInfo.push({ landId: id, plantId, name: plantName });
            } else if (showDebug) {
                console.log(`  [${friendName}] 土地#${id}: 成熟但stealable=false (可能已被偷过)`);
            }
            continue;
        }

        if (phaseVal === PlantPhase.DEAD) continue;

        // 帮助操作
        if (toNum(plant.dry_num) > 0) result.needWater.push(id);
        if (plant.weed_owners && plant.weed_owners.length > 0) result.needWeed.push(id);
        if (plant.insect_owners && plant.insect_owners.length > 0) result.needBug.push(id);

        // 捣乱操作: 检查是否可以放草/放虫
        // 条件: 没有草且我没放过草
        const weedOwners = plant.weed_owners || [];
        const insectOwners = plant.insect_owners || [];
        const iAlreadyPutWeed = weedOwners.some(gid => toNum(gid) === myGid);
        const iAlreadyPutBug = insectOwners.some(gid => toNum(gid) === myGid);

        // 每块地最多2个草/虫，且我没放过
        if (weedOwners.length < 2 && !iAlreadyPutWeed) {
            result.canPutWeed.push(id);
        }
        if (insectOwners.length < 2 && !iAlreadyPutBug) {
            result.canPutBug.push(id);
        }
    }
    return result;
}

/**
 * 获取好友列表 (供面板)
 */
async function getFriendsList() {
    try {
        const reply = await getAllFriends();
        const friends = reply.game_friends || [];
        const state = getUserState();
        return friends
            .filter(f => toNum(f.gid) !== state.gid && f.name !== '小小农夫' && f.remark !== '小小农夫')
            .map(f => ({
                gid: toNum(f.gid),
                name: f.remark || f.name || `GID:${toNum(f.gid)}`,
                plant: f.plant ? {
                    stealNum: toNum(f.plant.steal_plant_num),
                    dryNum: toNum(f.plant.dry_num),
                    weedNum: toNum(f.plant.weed_num),
                    insectNum: toNum(f.plant.insect_num),
                } : null,
            }))
            .sort((a, b) => {
                // 固定顺序：先按名称，再按 GID，避免刷新时顺序抖动
                const an = String(a.name || '');
                const bn = String(b.name || '');
                const byName = an.localeCompare(bn, 'zh-CN');
                if (byName !== 0) return byName;
                return Number(a.gid || 0) - Number(b.gid || 0);
            });
    } catch (e) {
        return [];
    }
}

/**
 * 获取指定好友的农田详情 (进入-获取-离开)
 */
async function getFriendLandsDetail(friendGid) {
    try {
        const enterReply = await enterFriendFarm(friendGid);
        const lands = enterReply.lands || [];
        const state = getUserState();
        const analyzed = analyzeFriendLands(lands, state.gid, '');
        await leaveFriendFarm(friendGid);

        const landsList = [];
        const nowSec = getServerTimeSec();
        for (const land of lands) {
            const id = toNum(land.id);
            const level = toNum(land.level);
            const unlocked = !!land.unlocked;
            if (!unlocked) {
                landsList.push({
                    id,
                    unlocked: false,
                    status: 'locked',
                    plantName: '',
                    phaseName: '未解锁',
                    level,
                    needWater: false,
                    needWeed: false,
                    needBug: false,
                });
                continue;
            }
            const plant = land.plant;
            if (!plant || !plant.phases || plant.phases.length === 0) {
                landsList.push({ id, unlocked: true, status: 'empty', plantName: '', phaseName: '空地', level });
                continue;
            }
            const currentPhase = getCurrentPhase(plant.phases, false, '');
            if (!currentPhase) {
                landsList.push({ id, unlocked: true, status: 'empty', plantName: '', phaseName: '', level });
                continue;
            }
            const phaseVal = currentPhase.phase;
            const plantId = toNum(plant.id);
            const plantName = getPlantName(plantId) || plant.name || '未知';
            const plantCfg = getPlantById(plantId);
            const seedId = toNum(plantCfg && plantCfg.seed_id);
            const seedImage = seedId > 0 ? getSeedImageBySeedId(seedId) : '';
            const phaseName = PHASE_NAMES[phaseVal] || '';
            const maturePhase = Array.isArray(plant.phases)
                ? plant.phases.find((p) => p && toNum(p.phase) === PlantPhase.MATURE)
                : null;
            const matureBegin = maturePhase ? toNum(maturePhase.begin_time) : 0;
            const matureInSec = matureBegin > nowSec ? (matureBegin - nowSec) : 0;
            let landStatus = 'growing';
            if (phaseVal === PlantPhase.MATURE) landStatus = plant.stealable ? 'stealable' : 'harvested';
            else if (phaseVal === PlantPhase.DEAD) landStatus = 'dead';

            landsList.push({
                id,
                unlocked: true,
                status: landStatus,
                plantName,
                seedId,
                seedImage,
                phaseName,
                level,
                matureInSec,
                needWater: toNum(plant.dry_num) > 0,
                needWeed: (plant.weed_owners && plant.weed_owners.length > 0),
                needBug: (plant.insect_owners && plant.insect_owners.length > 0),
            });
        }

        return {
            lands: landsList,
            summary: analyzed,
        };
    } catch (e) {
        return { lands: [], summary: {} };
    }
}

async function runBatchWithFallback(ids, batchFn, singleFn) {
    const target = Array.isArray(ids) ? ids.filter(Boolean) : [];
    if (target.length === 0) return 0;
    try {
        await batchFn(target);
        return target.length;
    } catch (e) {
        let ok = 0;
        for (const landId of target) {
            try {
                await singleFn([landId]);
                ok++;
            } catch (e2) { /* ignore */ }
            await sleep(100);
        }
        return ok;
    }
}

/**
 * 面板手动好友操作（单个好友）
 * opType: 'steal' | 'water' | 'weed' | 'bug' | 'bad'
 */
async function doFriendOperation(friendGid, opType) {
    const gid = toNum(friendGid);
    if (!gid) return { ok: false, message: '无效好友ID', opType };

    let enterReply;
    try {
        enterReply = await enterFriendFarm(gid);
    } catch (e) {
        return { ok: false, message: `进入好友农场失败: ${e.message}`, opType };
    }

    try {
        const lands = enterReply.lands || [];
        const state = getUserState();
        const status = analyzeFriendLands(lands, state.gid, '');
        let count = 0;

        if (opType === 'steal') {
            if (!status.stealable.length) return { ok: true, opType, count: 0, message: '没有可偷取土地' };
            const precheck = await checkCanOperateRemote(gid, 10008);
            if (!precheck.canOperate) return { ok: true, opType, count: 0, message: '今日偷菜次数已用完' };
            const maxNum = precheck.canStealNum > 0 ? precheck.canStealNum : status.stealable.length;
            const target = status.stealable.slice(0, maxNum);
            count = await runBatchWithFallback(target, (ids) => stealHarvest(gid, ids), (ids) => stealHarvest(gid, ids));
            if (count > 0) {
                recordOperation('steal', count);
                // 手动偷取成功后立即尝试出售一次果实
                try {
                    await sellAllFruits();
                } catch (e) {
                    logWarn('仓库', `手动偷取后自动出售失败: ${e.message}`, {
                        module: 'warehouse',
                        event: 'sell_after_steal',
                        result: 'error',
                        mode: 'manual',
                    });
                }
            }
            return { ok: true, opType, count, message: `偷取完成 ${count} 块` };
        }

        if (opType === 'water') {
            if (!status.needWater.length) return { ok: true, opType, count: 0, message: '没有可浇水土地' };
            const precheck = await checkCanOperateRemote(gid, 10007);
            if (!precheck.canOperate) return { ok: true, opType, count: 0, message: '今日浇水次数已用完' };
            count = await runBatchWithFallback(status.needWater, (ids) => helpWater(gid, ids), (ids) => helpWater(gid, ids));
            if (count > 0) recordOperation('helpWater', count);
            return { ok: true, opType, count, message: `浇水完成 ${count} 块` };
        }

        if (opType === 'weed') {
            if (!status.needWeed.length) return { ok: true, opType, count: 0, message: '没有可除草土地' };
            const precheck = await checkCanOperateRemote(gid, 10005);
            if (!precheck.canOperate) return { ok: true, opType, count: 0, message: '今日除草次数已用完' };
            count = await runBatchWithFallback(status.needWeed, (ids) => helpWeed(gid, ids), (ids) => helpWeed(gid, ids));
            if (count > 0) recordOperation('helpWeed', count);
            return { ok: true, opType, count, message: `除草完成 ${count} 块` };
        }

        if (opType === 'bug') {
            if (!status.needBug.length) return { ok: true, opType, count: 0, message: '没有可除虫土地' };
            const precheck = await checkCanOperateRemote(gid, 10006);
            if (!precheck.canOperate) return { ok: true, opType, count: 0, message: '今日除虫次数已用完' };
            count = await runBatchWithFallback(status.needBug, (ids) => helpInsecticide(gid, ids), (ids) => helpInsecticide(gid, ids));
            if (count > 0) recordOperation('helpBug', count);
            return { ok: true, opType, count, message: `除虫完成 ${count} 块` };
        }

        if (opType === 'bad') {
            let bugCount = 0;
            let weedCount = 0;
            if (!status.canPutBug.length && !status.canPutWeed.length) {
                return { ok: true, opType, count: 0, bugCount: 0, weedCount: 0, message: '没有可捣乱土地' };
            }

            // 手动捣乱不依赖预检查，逐块执行（与 terminal-farm-main 保持一致）
            let failDetails = [];
            if (status.canPutBug.length) {
                const bugRet = await putInsectsDetailed(gid, status.canPutBug);
                bugCount = bugRet.ok;
                failDetails = failDetails.concat((bugRet.failed || []).map(f => `放虫#${f.landId}:${f.reason}`));
                if (bugCount > 0) recordOperation('bug', bugCount);
            }
            if (status.canPutWeed.length) {
                const weedRet = await putWeedsDetailed(gid, status.canPutWeed);
                weedCount = weedRet.ok;
                failDetails = failDetails.concat((weedRet.failed || []).map(f => `放草#${f.landId}:${f.reason}`));
                if (weedCount > 0) recordOperation('weed', weedCount);
            }
            count = bugCount + weedCount;
            if (count <= 0) {
                const reasonPreview = failDetails.slice(0, 2).join(' | ');
                return {
                    ok: true,
                    opType,
                    count: 0,
                    bugCount,
                    weedCount,
                    message: reasonPreview ? `捣乱失败: ${reasonPreview}` : '捣乱失败或今日次数已用完'
                };
            }
            return { ok: true, opType, count, bugCount, weedCount, message: `捣乱完成 虫${bugCount}/草${weedCount}` };
        }

        return { ok: false, opType, count: 0, message: '未知操作类型' };
    } catch (e) {
        return { ok: false, opType, count: 0, message: e.message || '操作失败' };
    } finally {
        try { await leaveFriendFarm(gid); } catch (e) { /* ignore */ }
    }
}

// ============ 拜访好友 ============

async function visitFriend(friend, totalActions, myGid) {
    const { gid, name } = friend;
    const showDebug = DEBUG_FRIEND_LANDS === true || DEBUG_FRIEND_LANDS === name;

    if (showDebug) {
        console.log(`\n========== 调试: 进入好友 [${name}] 农场 ==========`);
    }

    let enterReply;
    try {
        enterReply = await enterFriendFarm(gid);
    } catch (e) {
        logWarn('好友', `进入 ${name} 农场失败: ${e.message}`, {
            module: 'friend', event: 'enter_farm', result: 'error', friendName: name, friendGid: gid
        });
        return;
    }

    const lands = enterReply.lands || [];
    if (showDebug) {
        console.log(`  [${name}] 获取到 ${lands.length} 块土地`);
    }
    if (lands.length === 0) {
        await leaveFriendFarm(gid);
        return;
    }

    const status = analyzeFriendLands(lands, myGid, name);
    
    if (showDebug) {
        console.log(`  [${name}] 分析结果: 可偷=${status.stealable.length} 浇水=${status.needWater.length} 除草=${status.needWeed.length} 除虫=${status.needBug.length}`);
        console.log(`========== 调试结束 ==========\n`);
    }

    // 执行操作
    const actions = [];

    // 帮助操作: 只在有经验时执行 (如果启用了 HELP_ONLY_WITH_EXP)
    // 并且检查细分开关
    const autoHelp = isAutomationOn('friend_help');
    
    if (autoHelp && status.needWeed.length > 0) {
        const shouldHelp = !HELP_ONLY_WITH_EXP || canGetExp(10005);  // 10005=除草
        if (shouldHelp) {
            const precheck = await checkCanOperateRemote(gid, 10005);
            if (precheck.canOperate) {
                let ok = 0;
                try {
                    await helpWeed(gid, status.needWeed);
                    ok = status.needWeed.length;
                } catch (e) {
                    for (const landId of status.needWeed) {
                        try { await helpWeed(gid, [landId]); ok++; } catch (e2) { /* ignore */ }
                        await sleep(100);
                    }
                }
                if (ok > 0) { actions.push(`草${ok}`); totalActions.weed += ok; recordOperation('helpWeed', ok); }
            }
        }
    }

    if (autoHelp && status.needBug.length > 0) {
        const shouldHelp = !HELP_ONLY_WITH_EXP || canGetExp(10006);  // 10006=除虫
        if (shouldHelp) {
            const precheck = await checkCanOperateRemote(gid, 10006);
            if (precheck.canOperate) {
                let ok = 0;
                try {
                    await helpInsecticide(gid, status.needBug);
                    ok = status.needBug.length;
                } catch (e) {
                    for (const landId of status.needBug) {
                        try { await helpInsecticide(gid, [landId]); ok++; } catch (e2) { /* ignore */ }
                        await sleep(100);
                    }
                }
                if (ok > 0) { actions.push(`虫${ok}`); totalActions.bug += ok; recordOperation('helpBug', ok); }
            }
        }
    }

    if (autoHelp && status.needWater.length > 0) {
        const shouldHelp = !HELP_ONLY_WITH_EXP || canGetExp(10007);  // 10007=浇水
        if (shouldHelp) {
            const precheck = await checkCanOperateRemote(gid, 10007);
            if (precheck.canOperate) {
                let ok = 0;
                try {
                    await helpWater(gid, status.needWater);
                    ok = status.needWater.length;
                } catch (e) {
                    for (const landId of status.needWater) {
                        try { await helpWater(gid, [landId]); ok++; } catch (e2) { /* ignore */ }
                        await sleep(100);
                    }
                }
                if (ok > 0) { actions.push(`水${ok}`); totalActions.water += ok; recordOperation('helpWater', ok); }
            }
        }
    }

    // 偷菜: 始终执行 (受 friend_steal 开关控制)
    if (isAutomationOn('friend_steal') && status.stealable.length > 0) {
        const precheck = await checkCanOperateRemote(gid, 10008);
        if (precheck.canOperate) {
            const canStealNum = precheck.canStealNum > 0 ? precheck.canStealNum : status.stealable.length;
            let ok = 0;
            const stolenPlants = [];
            const targetLands = status.stealable.slice(0, canStealNum);
            try {
                await stealHarvest(gid, targetLands);
                ok = targetLands.length;
                for (const landId of targetLands) {
                    const matchedInfo = status.stealableInfo.find(x => x.landId === landId);
                    if (matchedInfo) stolenPlants.push(matchedInfo.name);
                }
            } catch (e) {
                for (let i = 0; i < targetLands.length; i++) {
                    const landId = targetLands[i];
                    try {
                        await stealHarvest(gid, [landId]);
                        ok++;
                        const matchedInfo = status.stealableInfo.find(x => x.landId === landId);
                        if (matchedInfo) {
                            stolenPlants.push(matchedInfo.name);
                        }
                    } catch (e2) { /* ignore */ }
                    await sleep(100);
                }
            }
            if (ok > 0) {
                const plantNames = [...new Set(stolenPlants)].join('/');
                actions.push(`偷${ok}${plantNames ? '(' + plantNames + ')' : ''}`);
                totalActions.steal += ok;
                recordOperation('steal', ok);
            }
        }
    }

    // 捣乱操作: 放虫(10004)/放草(10003) (受 friend_bad 开关控制)
    const autoBad = isAutomationOn('friend_bad');
    if (autoBad && status.canPutBug.length > 0 && canOperate(10004)) {
        const remaining = getRemainingTimes(10004);
        const toProcess = status.canPutBug.slice(0, remaining);
        const ok = await putInsects(gid, toProcess);
        if (ok > 0) { actions.push(`放虫${ok}`); totalActions.putBug += ok; }
    }

    if (autoBad && status.canPutWeed.length > 0 && canOperate(10003)) {
        const remaining = getRemainingTimes(10003);
        const toProcess = status.canPutWeed.slice(0, remaining);
        const ok = await putWeeds(gid, toProcess);
        if (ok > 0) { actions.push(`放草${ok}`); totalActions.putWeed += ok; }
    }

    if (actions.length > 0) {
        log('好友', `${name}: ${actions.join('/')}`, {
            module: 'friend', event: 'visit_friend', result: 'ok', friendName: name, friendGid: gid, actions
        });
    }

    await leaveFriendFarm(gid);
}

// ============ 好友巡查主循环 ============

async function checkFriends() {
    const state = getUserState();
    if (isCheckingFriends || !state.gid || !isAutomationOn('friend')) return false;
    if (inFriendQuietHours()) {
        return false;
    }
    isCheckingFriends = true;

    // 检查是否跨日需要重置
    checkDailyReset();

    // 经验限制状态（移到有操作时才显示）

    try {
        const friendsReply = await getAllFriends();
        const friends = friendsReply.game_friends || [];
        if (friends.length === 0) { log('好友', '没有好友', { module: 'friend', event: 'friend_scan', result: 'empty' }); return false; }

        // 检查是否还有捣乱次数 (放虫/放草)
        const canPutBugOrWeed = canOperate(10004) || canOperate(10003);  // 10004=放虫, 10003=放草
        const autoBadEnabled = isAutomationOn('friend_bad');

        // 分两类：有预览信息的优先访问，其他的放后面（用于放虫放草）
        const priorityFriends = [];  // 有可偷/可帮助的好友
        const otherFriends = [];     // 其他好友（仅用于放虫放草）
        const visitedGids = new Set();
        
        for (const f of friends) {
            const gid = toNum(f.gid);
            if (gid === state.gid) continue;
            if (visitedGids.has(gid)) continue;
            const name = f.remark || f.name || `GID:${gid}`;
            const p = f.plant;

            const stealNum = p ? toNum(p.steal_plant_num) : 0;
            const dryNum = p ? toNum(p.dry_num) : 0;
            const weedNum = p ? toNum(p.weed_num) : 0;
            const insectNum = p ? toNum(p.insect_num) : 0;

            // 调试：显示指定好友的预览信息
            const showDebug = DEBUG_FRIEND_LANDS === true || DEBUG_FRIEND_LANDS === name;
            if (showDebug) {
                console.log(`[调试] 好友列表预览 [${name}]: steal=${stealNum} dry=${dryNum} weed=${weedNum} insect=${insectNum}`);
            }

            // 只加入有预览信息的好友
            if (stealNum > 0 || dryNum > 0 || weedNum > 0 || insectNum > 0) {
                priorityFriends.push({ gid, name });
                visitedGids.add(gid);
                if (showDebug) {
                    console.log(`[调试] 好友 [${name}] 加入优先列表 (位置: ${priorityFriends.length})`);
                }
            } else if (autoBadEnabled && canPutBugOrWeed) {
                // 没有预览信息但可以放虫放草（仅在开启放虫放草功能时）
                otherFriends.push({ gid, name });
                visitedGids.add(gid);
            }
        }
        
        // 合并列表：优先好友在前
        const friendsToVisit = [...priorityFriends, ...otherFriends];
        
        // 调试：检查目标好友位置
        if (DEBUG_FRIEND_LANDS && typeof DEBUG_FRIEND_LANDS === 'string') {
            const idx = friendsToVisit.findIndex(f => f.name === DEBUG_FRIEND_LANDS);
            if (idx >= 0) {
                const inPriority = idx < priorityFriends.length;
                console.log(`[调试] 好友 [${DEBUG_FRIEND_LANDS}] 位置: ${idx + 1}/${friendsToVisit.length} (${inPriority ? '优先列表' : '其他列表'})`);
            } else {
                console.log(`[调试] 好友 [${DEBUG_FRIEND_LANDS}] 不在待访问列表中!`);
            }
        }

        if (friendsToVisit.length === 0) {
            // 无需操作时不输出日志
            return false;
        }

        let totalActions = { steal: 0, water: 0, weed: 0, bug: 0, putBug: 0, putWeed: 0 };
        for (let i = 0; i < friendsToVisit.length; i++) {
            const friend = friendsToVisit[i];
            const showDebug = DEBUG_FRIEND_LANDS === true || DEBUG_FRIEND_LANDS === friend.name;
            if (showDebug) {
                console.log(`[调试] 准备访问 [${friend.name}] (${i + 1}/${friendsToVisit.length})`);
            }
            try { 
                await visitFriend(friend, totalActions, state.gid); 
            } catch (e) { 
                if (showDebug) {
                    console.log(`[调试] 访问 [${friend.name}] 出错: ${e.message}`);
                }
            }
            await sleep(500);
            // 如果捣乱次数用完了，且没有其他操作，可以提前结束
            if (!canOperate(10004) && !canOperate(10003)) {  // 10004=放虫, 10003=放草
                // 继续巡查，但不再放虫放草
            }
        }

        // 自动模式：整轮好友偷取完成后再统一出售一次果实
        if (totalActions.steal > 0) {
            try {
                await sellAllFruits();
            } catch (e) {
                logWarn('仓库', `好友巡查后自动出售失败: ${e.message}`, {
                    module: 'warehouse',
                    event: 'sell_after_steal',
                    result: 'error',
                    mode: 'auto',
                    stealCount: totalActions.steal,
                });
            }
        }

        // 只在有操作时输出日志
        const summary = [];
        if (totalActions.steal > 0) summary.push(`偷${totalActions.steal}`);
        if (totalActions.weed > 0) summary.push(`除草${totalActions.weed}`);
        if (totalActions.bug > 0) summary.push(`除虫${totalActions.bug}`);
        if (totalActions.water > 0) summary.push(`浇水${totalActions.water}`);
        if (totalActions.putBug > 0) summary.push(`放虫${totalActions.putBug}`);
        if (totalActions.putWeed > 0) summary.push(`放草${totalActions.putWeed}`);
        
        if (summary.length > 0) {
            log('好友', `巡查 ${friendsToVisit.length} 人 → ${summary.join('/')}`, {
                module: 'friend', event: 'friend_cycle', result: 'ok', visited: friendsToVisit.length, summary
            });
        }
        isFirstFriendCheck = false;
        return summary.length > 0;
    } catch (err) {
        logWarn('好友', `巡查失败: ${err.message}`, {
            module: 'friend', event: 'friend_cycle', result: 'error'
        });
        return false;
    } finally {
        isCheckingFriends = false;
    }
}

/**
 * 好友巡查循环 - 本次完成后等待指定秒数再开始下次
 */
async function friendCheckLoop() {
    if (externalSchedulerMode) return;
    if (!friendLoopRunning) return;
    await checkFriends();
    if (!friendLoopRunning) return;
    friendCheckTimer = setTimeout(() => friendCheckLoop(), Math.max(0, CONFIG.friendCheckInterval));
}

function startFriendCheckLoop(options = {}) {
    if (friendLoopRunning) return;
    externalSchedulerMode = !!options.externalScheduler;
    friendLoopRunning = true;

    // 注册操作限制更新回调，从农场检查中获取限制信息
    setOperationLimitsCallback(updateOperationLimits);

    // 监听好友申请推送 (微信同玩)
    networkEvents.on('friendApplicationReceived', onFriendApplicationReceived);

    if (!externalSchedulerMode) {
        // 延迟 5 秒后启动循环，等待登录和首次农场检查完成
        friendCheckTimer = setTimeout(() => friendCheckLoop(), 5000);
    }

    // 启动时检查一次待处理的好友申请
    setTimeout(() => checkAndAcceptApplications(), 3000);
}

function stopFriendCheckLoop() {
    friendLoopRunning = false;
    externalSchedulerMode = false;
    networkEvents.off('friendApplicationReceived', onFriendApplicationReceived);
    if (friendCheckTimer) { clearTimeout(friendCheckTimer); friendCheckTimer = null; }
}

function refreshFriendCheckLoop(delayMs = 200) {
    if (!friendLoopRunning || externalSchedulerMode) return;
    if (friendCheckTimer) {
        clearTimeout(friendCheckTimer);
        friendCheckTimer = null;
    }
    friendCheckTimer = setTimeout(() => friendCheckLoop(), Math.max(0, delayMs));
}

// ============ 自动同意好友申请 (微信同玩) ============

/**
 * 处理服务器推送的好友申请
 */
function onFriendApplicationReceived(applications) {
    const names = applications.map(a => a.name || `GID:${toNum(a.gid)}`).join(', ');
    log('申请', `收到 ${applications.length} 个好友申请: ${names}`);

    // 自动同意
    const gids = applications.map(a => toNum(a.gid));
    acceptFriendsWithRetry(gids);
}

/**
 * 检查并同意所有待处理的好友申请
 */
async function checkAndAcceptApplications() {
    try {
        const reply = await getApplications();
        const applications = reply.applications || [];
        if (applications.length === 0) return;

        const names = applications.map(a => a.name || `GID:${toNum(a.gid)}`).join(', ');
        log('申请', `发现 ${applications.length} 个待处理申请: ${names}`);

        const gids = applications.map(a => toNum(a.gid));
        await acceptFriendsWithRetry(gids);
    } catch (e) {
        // 静默失败，可能是 QQ 平台不支持
    }
}

/**
 * 同意好友申请 (带重试)
 */
async function acceptFriendsWithRetry(gids) {
    if (gids.length === 0) return;
    try {
        const reply = await acceptFriends(gids);
        const friends = reply.friends || [];
        if (friends.length > 0) {
            const names = friends.map(f => f.name || f.remark || `GID:${toNum(f.gid)}`).join(', ');
            log('申请', `已同意 ${friends.length} 人: ${names}`);
        }
    } catch (e) {
        logWarn('申请', `同意失败: ${e.message}`);
    }
}

module.exports = {
    checkFriends, startFriendCheckLoop, stopFriendCheckLoop,
    refreshFriendCheckLoop,
    checkAndAcceptApplications,
    getOperationLimits,
    getFriendsList,
    getFriendLandsDetail,
    doFriendOperation,
};
