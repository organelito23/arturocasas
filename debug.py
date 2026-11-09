import asyncio, json, time, collections, websockets
async def main():
    cnt, samples, first = collections.Counter(), {}, None
    async with websockets.connect("wss://pumpportal.fun/api/data", max_size=None) as ws:
        await ws.send(json.dumps({"method": "subscribeNewToken"}))
        t0 = time.time()
        while time.time() - t0 < 90:
            m = json.loads(await asyncio.wait_for(ws.recv(), 30))
            k = m.get("txType") or ("message:" + str(m.get("message", m))[:80])
            cnt[k] += 1
            samples.setdefault(k, m)
            if m.get("txType") == "create" and not first:
                first = m["mint"]
                await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [first]}))
    print(dict(cnt))
    for k, v in samples.items():
        print(k, json.dumps(v)[:600])
asyncio.run(main())
