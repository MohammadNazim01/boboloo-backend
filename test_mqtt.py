import asyncio
import sys

from aiomqtt import Client


# =========================================
# WINDOWS FIX
# =========================================

if sys.platform == "win32":

    asyncio.set_event_loop_policy(
        asyncio.WindowsSelectorEventLoopPolicy()
    )


# =========================================
# TOY CONFIG
# =========================================

toy_id = "toy_001"

input_topic = (
    f"boboloo/toy/{toy_id}/audio/in"
)

output_topic = (
    f"boboloo/toy/{toy_id}/audio/out"
)


async def main():

    try:

        async with Client(
            "broker.hivemq.com",
            port=1883
        ) as client:

            # subscribe for toy response
            await client.subscribe(
                output_topic
            )

            print(
                f"🚀 Toy {toy_id} online"
            )

            while True:

                # ask question dynamically
                question = input(
                    "\n🎤 Ask Boboloo: "
                )

                await client.publish(
                    input_topic,
                    question
                )

                messages = client.messages

                async for message in messages:

                    print(
                        "\n🤖 Boboloo:",
                        message.payload.decode()
                    )

                    break

    except Exception as e:

        print(f"❌ MQTT Error: {e}")


if __name__ == "__main__":

    asyncio.run(main())