import asyncio
from voice.brain.providers.gemini import GeminiProvider

async def main():
    p = GeminiProvider()
    print("Testing Gemini...")
    turn = await p.unified_turn(
        transcript="ನಮ್ಮ ಊರಿನಲ್ಲಿ ರಸ್ತೆ ತುಂಬಾ ಹಾಳಾಗಿದೆ",
        language="kn-IN",
        dialect_hint="bengaluru",
        history=[]
    )
    print(f"Success! Intent: {turn.issue_summary}, Urgency: {turn.urgency}")

asyncio.run(main())
