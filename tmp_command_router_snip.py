with open('services/command_router.py', encoding='utf-8') as f:
    for i,line in enumerate(f,1):
        if 1670 <= i <= 1705:
            print(f"{i}: {line.rstrip()}")
