with open('services/render_payload_builder.py', encoding='utf-8') as f:
    for i,line in enumerate(f,1):
        if 1 <= i <= 200:
            print(f"{i}: {line.rstrip()}")
