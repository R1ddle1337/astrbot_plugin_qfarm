from itertools import islice
with open('main.py', encoding='utf-8') as f:
    for i,line in enumerate(f,1):
        if 160 <= i <= 220:
            print(f"{i}: {line.rstrip()}")
