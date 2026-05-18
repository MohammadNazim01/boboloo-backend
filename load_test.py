import asyncio
import random
import sys
import time

from aiomqtt import Client

# =========================================
# WINDOWS FIX
# =========================================

if sys.platform == "win32":

    asyncio.set_event_loop_policy(
        asyncio.WindowsSelectorEventLoopPolicy()
    )

# =========================================
# CONFIG
# =========================================

BROKER = "broker.hivemq.com"
PORT = 1883

TOTAL_TOYS = 1

QUESTIONS = [
    "Why sky is blue?",
    "Tell me a space story",
    "Why do stars shine?",
    "Tell me about dinosaurs",
    "Can cars fly?",
    "Why moon follows us?",
    "Tell me a funny joke",
    "How birds fly?",
]

# =========================================
# STATS
# =========================================

success_count = 0
failure_count = 0

# =========================================
# SINGLE TOY SIMULATION
# =========================================

async def simulate_toy(toy_number):

    global success_count
    global failure_count

    toy_id = f"TOY_{toy_number}"

    input_topic = (
        f"boboloo/toy/{toy_id}/audio/in"
    )

    output_topic = (
        f"boboloo/toy/{toy_id}/audio/out"
    )

    try:

        async with Client(
            BROKER,
            port=PORT
        ) as client:

            await client.subscribe(
                output_topic
            )

            print(
                f"🧸 {toy_id} connected"
            )

            while True:

                question = random.choice(
                    QUESTIONS
                )

                print(
                    f"\n📤 {toy_id}: "
                    f"{question}"
                )

                # =========================
                # LATENCY TIMER START
                # =========================

                start_time = time.time()

                await client.publish(
                    input_topic,
                    question
                )

                try:

                    # =====================
                    # WAIT FOR RESPONSE
                    # =====================

                    messages = client.messages

                    async with asyncio.timeout(20):

                        async for message in messages:

                            latency = (
                                time.time()
                                - start_time
                            )

                            response = (
                                message.payload
                                .decode()
                            )

                            print(
                                f"🤖 {toy_id}: "
                                f"{response}"
                            )

                            print(
                                f"⚡ {toy_id} "
                                f"Latency: "
                                f"{latency:.2f}s"
                            )

                            success_count += 1

                            break

                except TimeoutError:

                    failure_count += 1

                    print(
                        f"⏰ {toy_id} "
                        f"Response timeout"
                    )

                # =========================
                # RANDOM DELAY
                # =========================

                await asyncio.sleep(
                    random.randint(2, 5)
                )

    except Exception as e:

        failure_count += 1

        print(
            f"❌ {toy_id} error: {e}"
        )

# =========================================
# STATS MONITOR
# =========================================

async def stats_monitor():

    while True:

        print("\n" + "=" * 50)

        print(
            f"✅ Success: "
            f"{success_count}"
        )

        print(
            f"❌ Failures: "
            f"{failure_count}"
        )

        print("=" * 50)

        await asyncio.sleep(15)

# =========================================
# MAIN
# =========================================

async def main():

    tasks = []

    # =========================
    # START TOYS
    # =========================

    for i in range(
        1,
        TOTAL_TOYS + 1
    ):

        tasks.append(
            simulate_toy(i)
        )

    # =========================
    # STATS TASK
    # =========================

    tasks.append(
        stats_monitor()
    )

    await asyncio.gather(*tasks)

# =========================================
# ENTRYPOINT
# =========================================

if __name__ == "__main__":

    asyncio.run(main())