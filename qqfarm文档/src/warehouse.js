/**
 * 仓库系统 - 自动出售果实
 * 协议说明：BagReply 使用 item_bag（ItemBag），item_bag.items 才是背包物品列表
 */

const { types } = require('./proto');
const { sendMsgAsync, networkEvents, getUserState } = require('./network');
const { toLong, toNum, log, logWarn, sleep } = require('./utils');
const { updateStatusGold } = require('./status');
const { getFruitName, getPlantByFruitId, getPlantBySeedId, getItemById, getItemImageById } = require('./gameConfig');
const { isAutomationOn } = require('./store');

const SELL_BATCH_SIZE = 15;

// ============ API ============

async function getBag() {
    const body = types.BagRequest.encode(types.BagRequest.create({})).finish();
    const { body: replyBody } = await sendMsgAsync('gamepb.itempb.ItemService', 'Bag', body);
    return types.BagReply.decode(replyBody);
}

function toSellItem(item) {
    const id = item.id != null ? toLong(item.id) : undefined;
    const count = item.count != null ? toLong(item.count) : undefined;
    const uid = item.uid != null ? toLong(item.uid) : undefined;
    return { id, count, uid };
}

async function sellItems(items) {
    const payload = items.map(toSellItem);
    const body = types.SellRequest.encode(types.SellRequest.create({ items: payload })).finish();
    const { body: replyBody } = await sendMsgAsync('gamepb.itempb.ItemService', 'Sell', body);
    return types.SellReply.decode(replyBody);
}

function isFruitItemId(id) {
    return !!getPlantByFruitId(Number(id));
}

function getBagItems(bagReply) {
    if (bagReply && bagReply.item_bag && bagReply.item_bag.items && bagReply.item_bag.items.length) {
        return bagReply.item_bag.items;
    }
    return bagReply && bagReply.items ? bagReply.items : [];
}

function getGoldFromItems(items) {
    for (const item of (items || [])) {
        const id = toNum(item.id);
        if (id === 1 || id === 1001) {
            const count = toNum(item.count);
            if (count > 0) return count;
        }
    }
    return 0;
}

function deriveGoldGainFromSellReply(reply, lastKnownGold) {
    const gainFromGetItems = getGoldFromItems((reply && reply.get_items) || []);
    if (gainFromGetItems > 0) {
        // get_items 通常就是本次获得值
        return { gain: gainFromGetItems, nextKnownGold: lastKnownGold };
    }

    // 兼容旧 proto/旧结构
    const currentOrDelta = getGoldFromItems((reply && (reply.items || reply.sell_items)) || []);
    if (currentOrDelta <= 0) return { gain: 0, nextKnownGold: lastKnownGold };

    // 协议在不同场景下可能返回“当前总金币”或“本次变化值”
    if (lastKnownGold > 0 && currentOrDelta >= lastKnownGold) {
        return { gain: currentOrDelta - lastKnownGold, nextKnownGold: currentOrDelta };
    }
    return { gain: currentOrDelta, nextKnownGold: lastKnownGold };
}

function getCurrentTotals() {
    const state = getUserState() || {};
    return {
        gold: Number(state.gold || 0),
        exp: Number(state.exp || 0),
    };
}

async function getCurrentTotalsFromBag() {
    const bagReply = await getBag();
    const items = getBagItems(bagReply);
    let gold = null;
    let exp = null;
    for (const item of items) {
        const id = toNum(item.id);
        const count = toNum(item.count);
        if (id === 1 || id === 1001) gold = count;       // 金币
        if (id === 1101) exp = count;     // 累计经验
    }
    return { gold, exp };
}

async function getBagDetail() {
    const bagReply = await getBag();
    const rawItems = getBagItems(bagReply);
    const merged = new Map();
    for (const it of (rawItems || [])) {
        const id = toNum(it.id);
        const count = toNum(it.count);
        if (id <= 0 || count <= 0) continue;
        const info = getItemById(id) || null;
        let name = info && info.name ? String(info.name) : '';
        let category = 'item';
        if (id === 1 || id === 1001) {
            name = '金币';
            category = 'gold';
        } else if (id === 1101) {
            name = '经验';
            category = 'exp';
        } else if (getPlantByFruitId(id)) {
            if (!name) name = `${getFruitName(id)}果实`;
            category = 'fruit';
        } else if (getPlantBySeedId(id)) {
            const p = getPlantBySeedId(id);
            if (!name) name = `${p && p.name ? p.name : '未知'}种子`;
            category = 'seed';
        }
        if (!name) name = `物品${id}`;
        const interactionType = info && info.interaction_type ? String(info.interaction_type) : '';

        if (!merged.has(id)) {
            merged.set(id, {
                id,
                count: 0,
                uid: 0, // 合并展示后 UID 不再有意义
                name,
                image: getItemImageById(id),
                category,
                itemType: info ? (Number(info.type) || 0) : 0,
                price: info ? (Number(info.price) || 0) : 0,
                level: info ? (Number(info.level) || 0) : 0,
                interactionType,
                hoursText: '',
            });
        }
        const row = merged.get(id);
        row.count += count;
    }

    const items = Array.from(merged.values()).map((row) => {
        if (row.interactionType === 'fertilizerbucket' && row.count > 0) {
            // 游戏显示更接近截断到 1 位小数（非四舍五入）
            const hoursFloor1 = Math.floor((row.count / 3600) * 10) / 10;
            row.hoursText = `${hoursFloor1.toFixed(1)}小时`;
        } else {
            row.hoursText = '';
        }
        return row;
    });
    items.sort((a, b) => {
        const ca = Number(a.count || 0);
        const cb = Number(b.count || 0);
        if (cb !== ca) return cb - ca;
        return Number(a.id || 0) - Number(b.id || 0);
    });
    return { totalKinds: items.length, items };
}

// ============ 出售逻辑 ============

/**
 * 检查并出售所有果实
 */
async function sellAllFruits() {
    const sellEnabled = isAutomationOn('sell');
    if (!sellEnabled) {
        return;
    }
    try {
        const bagReply = await getBag();
        const items = getBagItems(bagReply);

        const toSell = [];
        const names = [];
        for (const item of items) {
            const id = toNum(item.id);
            const count = toNum(item.count);
            const uid = item.uid ? toNum(item.uid) : 0;
            if (isFruitItemId(id) && count > 0) {
                if (uid === 0) {
                    logWarn('仓库', `跳过无效物品: ID=${id} Count=${count} (UID丢失)`);
                    continue;
                }
                toSell.push(item);
                names.push(`${getFruitName(id)}x${count}`);
            }
        }

        if (toSell.length === 0) {
            log('仓库', '无果实可出售');
            return;
        }

        const totalsBefore = getCurrentTotals();
        const goldBefore = totalsBefore.gold;
        let serverGoldTotal = 0;
        let knownGold = goldBefore;
        for (let i = 0; i < toSell.length; i += SELL_BATCH_SIZE) {
            const batch = toSell.slice(i, i + SELL_BATCH_SIZE);
            try {
                const reply = await sellItems(batch);
                const inferred = deriveGoldGainFromSellReply(reply, knownGold);
                const gained = Math.max(0, toNum(inferred.gain));
                knownGold = inferred.nextKnownGold;
                if (gained > 0) serverGoldTotal += gained;
            } catch (batchErr) {
                // 某个条目可能参数非法，降级为逐个出售，跳过错误条目
                logWarn('仓库', `批量出售失败，改为逐个重试: ${batchErr.message}`);
                for (const it of batch) {
                    try {
                        const singleReply = await sellItems([it]);
                        const inferred = deriveGoldGainFromSellReply(singleReply, knownGold);
                        const gained = Math.max(0, toNum(inferred.gain));
                        knownGold = inferred.nextKnownGold;
                        if (gained > 0) serverGoldTotal += gained;
                    } catch (singleErr) {
                        const sid = toNum(it.id);
                        const sc = toNum(it.count);
                        logWarn('仓库', `跳过不可售物品: ID=${sid} x${sc} (${singleErr.message})`, {
                            module: 'warehouse',
                            event: 'sell_skip_invalid',
                            result: 'skip',
                            itemId: sid,
                            count: sc,
                        });
                    }
                }
            }
            if (i + SELL_BATCH_SIZE < toSell.length) await sleep(300);
        }
        // 等待金币通知更新（最多 2s）
        let goldAfter = goldBefore;
        const startWait = Date.now();
        while (Date.now() - startWait < 2000) {
            const currentGold = (getUserState() && getUserState().gold) ? getUserState().gold : goldAfter;
            if (currentGold !== goldBefore) {
                goldAfter = currentGold;
                break;
            }
            await sleep(200);
        }
        const totalsAfter = getCurrentTotals();
        const totalGoldDelta = goldAfter > goldBefore ? (goldAfter - goldBefore) : 0;
        const totalsDeltaGold = totalsAfter.gold - totalsBefore.gold;
        const totalsDeltaExp = totalsAfter.exp - totalsBefore.exp;

        // 通知缺失时，尝试从背包读取金币做最终兜底
        let bagDelta = 0;
        if (totalGoldDelta <= 0 && serverGoldTotal <= 0) {
            try {
                const bagAfter = await getBag();
                const bagGold = getGoldFromItems(getBagItems(bagAfter));
                if (bagGold > goldBefore) bagDelta = bagGold - goldBefore;
            } catch (e) {}
        }

        const totalGoldEarned = Math.max(serverGoldTotal, totalGoldDelta, bagDelta);
        if (totalGoldDelta <= 0 && totalGoldEarned > 0) {
            // 某些情况下 ItemNotify 丢失，使用出售回包做金币兜底同步
            const state = getUserState();
            if (state) {
                state.gold = Number(state.gold || 0) + totalGoldEarned;
                updateStatusGold(state.gold);
            }
        }
        log('仓库', `出售 ${names.join(', ')}${totalGoldEarned > 0 ? `，获得 ${totalGoldEarned} 金币` : ''}`, {
            module: 'warehouse',
            event: totalGoldEarned > 0 ? 'sell_success' : 'sell_done',
            result: totalGoldEarned > 0 ? 'ok' : 'unknown_gain',
            count: toSell.length,
            gold: totalGoldEarned,
            totalsBefore,
            totalsAfter,
            totalsDeltaGold,
            totalsDeltaExp,
        });
        
        // 发送出售事件，用于统计金币收益
        if (totalGoldEarned > 0) {
            networkEvents.emit('sell', totalGoldEarned);
        }
    } catch (e) {
        logWarn('仓库', `出售失败: ${e.message}`);
    }
}

// 手动触发一次出售（用于调试）
async function debugSellFruits() {
    try {
        log('仓库', '正在检查背包...');
        const bagReply = await getBag();
        const items = getBagItems(bagReply);
        log('仓库', `背包共 ${items.length} 种物品`);

        // 显示所有物品（包含 uid）
        for (const item of items) {
            const id = toNum(item.id);
            const count = toNum(item.count);
            const uid = item.uid ? toNum(item.uid) : 0;
            const isFruit = isFruitItemId(id);
            const name = isFruit ? getFruitName(id) : '非果实';
            log('仓库', `  [${isFruit ? '果实' : '物品'}] ${name}(${id}) x${count} uid=${uid}`);
        }

        const toSell = [];
        const names = [];
        for (const item of items) {
            const id = toNum(item.id);
            const count = toNum(item.count);
            const uid = item.uid ? toNum(item.uid) : 0;
            if (isFruitItemId(id) && count > 0) {
                if (uid === 0) {
                    logWarn('仓库', `跳过无效物品: ID=${id} Count=${count} (UID丢失)`);
                    continue;
                }
                toSell.push(item);
                names.push(`${getFruitName(id)}x${count}`);
            }
        }

        if (toSell.length === 0) {
            log('仓库', '没有果实可出售');
            return;
        }

        log('仓库', `准备出售 ${toSell.length} 种果实，每批 ${SELL_BATCH_SIZE} 条...`);
        let totalGold = 0;
        let knownGold = Number((getUserState() && getUserState().gold) || 0);
        for (let i = 0; i < toSell.length; i += SELL_BATCH_SIZE) {
            const batch = toSell.slice(i, i + SELL_BATCH_SIZE);
            const reply = await sellItems(batch);
            const inferred = deriveGoldGainFromSellReply(reply, knownGold);
            const g = Math.max(0, toNum(inferred.gain));
            knownGold = inferred.nextKnownGold;
            totalGold += g;
            log('仓库', `  第 ${Math.floor(i / SELL_BATCH_SIZE) + 1} 批: 获得 ${g} 金币`);
            if (i + SELL_BATCH_SIZE < toSell.length) await sleep(300);
        }
        log('仓库', `出售 ${names.join(', ')}，获得 ${totalGold} 金币`);
        
        // 发送出售事件，用于统计金币收益
        if (totalGold > 0) {
            networkEvents.emit('sell', totalGold);
        }
    } catch (e) {
        logWarn('仓库', `调试出售失败: ${e.message}`);
        console.error(e);
    }
}

module.exports = {
    getBag,
    getBagDetail,
    sellItems,
    sellAllFruits,
    debugSellFruits,
    getBagItems,
    getCurrentTotalsFromBag,
};
