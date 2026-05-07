import asyncio
import websockets
import json
import base64
import audioop
import time

WS_URL = "wss://arivu-voice-1074461335052.asia-south1.run.app/ws"

async def test_prod_ws():
    print(f"Connecting to {WS_URL}...")
    async with websockets.connect(WS_URL) as ws:
        print("Connected! Sending connected and start events...")
        
        # Twilio sends 'connected' and 'start'
        await ws.send(json.dumps({
            "event": "connected",
            "protocol": "Call",
            "version": "1.0.0"
        }))
        
        await ws.send(json.dumps({
            "event": "start",
            "sequenceNumber": "1",
            "start": {
                "accountSid": "AC_TEST",
                "streamSid": "MZ_TEST",
                "callSid": "CA_TEST",
                "tracks": ["inbound"],
                "mediaFormat": {
                    "encoding": "audio/x-mulaw",
                    "sampleRate": 8000,
                    "channels": 1
                }
            },
            "streamSid": "MZ_TEST"
        }))
        
        # Start a task to receive messages
        async def receive_loop():
            try:
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    event = data.get("event")
                    if event == "media":
                        print(f"<- Received media chunk ({len(data['media']['payload'])} bytes b64)")
                    elif event == "mark":
                        print(f"<- Received mark: {data['mark']['name']}")
                    else:
                        print(f"<- Received: {data}")
            except Exception as e:
                print(f"Receive loop ended: {e}")

        recv_task = asyncio.create_task(receive_loop())

        print("Waiting for greeting...")
        await asyncio.sleep(5)  # Wait for greeting to finish

        print("Sending audio (simulating a complaint)...")
        # Generate some fake audio (a simple sine wave) to trigger VAD
        # 16kHz sine wave -> downsample to 8kHz -> mulaw
        import math
        sample_rate = 8000
        duration_s = 3.0
        frequency = 440.0
        num_samples = int(sample_rate * duration_s)
        
        pcm_bytes = bytearray()
        for i in range(num_samples):
            # Generate 16-bit PCM
            val = int(32767 * math.sin(2 * math.pi * frequency * i / sample_rate))
            pcm_bytes.extend(val.to_bytes(2, byteorder='little', signed=True))
            
        # Convert PCM to mulaw
        mulaw_bytes = audioop.lin2ulaw(bytes(pcm_bytes), 2)
        
        # Send in 20ms chunks (160 bytes)
        chunk_size = 160
        for i in range(0, len(mulaw_bytes), chunk_size):
            chunk = mulaw_bytes[i:i+chunk_size]
            await ws.send(json.dumps({
                "event": "media",
                "sequenceNumber": str(i//chunk_size + 2),
                "media": {
                    "track": "inbound",
                    "chunk": str(i//chunk_size + 1),
                    "timestamp": str(int(time.time() * 1000)),
                    "payload": base64.b64encode(chunk).decode("ascii")
                },
                "streamSid": "MZ_TEST"
            }))
            await asyncio.sleep(0.02) # Simulate real-time streaming
            
        print("Sent audio. Now waiting for silence to trigger VAD...")
        # Send silence to trigger end-of-speech
        silence_duration_s = 1.0
        silence_samples = int(sample_rate * silence_duration_s)
        silence_pcm = bytearray(b'\x00\x00' * silence_samples)
        silence_mulaw = audioop.lin2ulaw(bytes(silence_pcm), 2)
        
        for i in range(0, len(silence_mulaw), chunk_size):
            chunk = silence_mulaw[i:i+chunk_size]
            await ws.send(json.dumps({
                "event": "media",
                "media": {
                    "payload": base64.b64encode(chunk).decode("ascii")
                },
                "streamSid": "MZ_TEST"
            }))
            await asyncio.sleep(0.02)
            
        print("Waiting for response from server...")
        await asyncio.sleep(15)
        
        print("Ending call...")
        await ws.send(json.dumps({
            "event": "stop",
            "streamSid": "MZ_TEST"
        }))
        
        recv_task.cancel()

if __name__ == "__main__":
    asyncio.run(test_prod_ws())
