#!/usr/bin/env python3
"""
Boboloo Mock Toy Simulator
===========================
Simulates one or many ESP32 toys entirely in Python — no hardware needed.

Each simulated toy:
  - Connects to the MQTT broker (local EMQX or public HiveMQ)
  - Authenticates with username=factory_device_id, password=toy_api_key
  - Sends periodic heartbeat status messages
  - Sends child questions on the audio/in topic
  - Listens for AI replies on audio/out
  - Listens for OTA commands on cmd topic
  - Reports OTA result back via status topic
  - Supports simulated WiFi drops and reconnects

Usage:
  # Single toy, interactive mode
  python dev/toy_simulator.py --device TEST001 --key <toy_api_key>

  # 10 toys, automatic random questions every 5s
  python dev/toy_simulator.py --count 10 --auto --interval 5

  # Stress test: 50 toys, rapid fire
  python dev/toy_simulator.py --count 50 --auto --interval 1

  # Simulate WiFi drops
  python dev/toy_simulator.py --device TEST001 --key <key> --drop-rate 0.1

  # Simulate OTA flow (send fake OTA command to yourself)
  python dev/toy_simulator.py --device TEST001 --key <key> --ota-test
"""

import argparse
import asyncio
import json
import logging
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import gmqtt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("toy_sim")

# ─── Toy questions ────────────────────────────────────────────────────────────

QUESTIONS = [
    "Why is the sky blue?",
    "How do birds fly?",
    "Why does the moon follow us?",
    "Tell me a story about dinosaurs",
    "Why do stars shine?",
    "What is inside the sun?",
    "Why do we dream?",
    "How do fish breathe underwater?",
    "Why is grass green?",
    "Can robots feel feelings?",
    "Why does it rain?",
    "What makes rainbows?",
    "Why do we have two eyes?",
    "How do planes stay up?",
]

# ─── Per-toy state ────────────────────────────────────────────────────────────

@dataclass
class ToyState:
    device_id: str
    api_key: str
    broker: str
    port: int
    use_tls: bool
    drop_rate: float = 0.0

    client: Optional[gmqtt.Client] = field(default=None, repr=False)
    connected: bool = False
    questions_sent: int = 0
    replies_received: int = 0
    ota_in_progress: bool = False
    battery: int = field(default_factory=lambda: random.randint(60, 100))
    wifi_rssi: int = field(default_factory=lambda: random.randint(-70, -40))

    # latency tracking
    _last_question_time: float = 0.0
    latencies: list = field(default_factory=list)

    @property
    def topic_audio_in(self):
        return f"boboloo/toy/{self.device_id}/audio/in"

    @property
    def topic_audio_out(self):
        return f"boboloo/toy/{self.device_id}/audio/out"

    @property
    def topic_status(self):
        return f"boboloo/toy/{self.device_id}/status"

    @property
    def topic_cmd(self):
        return f"boboloo/toy/{self.device_id}/cmd"

    @property
    def avg_latency(self):
        return sum(self.latencies) / len(self.latencies) if self.latencies else 0

# ─── MQTT callbacks ───────────────────────────────────────────────────────────

def make_callbacks(toy: ToyState):

    def on_connect(client, flags, rc, properties):
        toy.connected = True
        logger.info(f"[{toy.device_id}] Connected (rc={rc})")
        client.subscribe(toy.topic_audio_out, qos=1)
        client.subscribe(toy.topic_cmd, qos=1)

    def on_disconnect(client, packet, exc=None):
        toy.connected = False
        if exc:
            logger.warning(f"[{toy.device_id}] Disconnected: {exc}")
        else:
            logger.info(f"[{toy.device_id}] Disconnected cleanly")

    def on_message(client, topic, payload, qos, properties):
        data = payload.decode("utf-8", errors="replace")

        if topic == toy.topic_audio_out:
            latency = time.time() - toy._last_question_time
            toy.replies_received += 1
            toy.latencies.append(latency)
            logger.info(
                f"[{toy.device_id}] REPLY ({latency:.2f}s): {data[:80]}"
            )

        elif topic == toy.topic_cmd:
            _handle_cmd(toy, client, data)

    return on_connect, on_disconnect, on_message


def _handle_cmd(toy: ToyState, client: gmqtt.Client, raw: str):
    try:
        cmd = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"[{toy.device_id}] Invalid cmd JSON: {raw}")
        return

    cmd_type = cmd.get("type")
    if cmd_type == "ota":
        version  = cmd.get("version", "unknown")
        url      = cmd.get("url", "")
        sha256   = cmd.get("sha256", "")
        size     = cmd.get("size", 0)
        logger.info(
            f"[{toy.device_id}] OTA command received: version={version} "
            f"size={size} bytes"
        )
        asyncio.ensure_future(_simulate_ota(toy, client, version, sha256))
    else:
        logger.warning(f"[{toy.device_id}] Unknown cmd type: {cmd_type}")


async def _simulate_ota(toy: ToyState, client: gmqtt.Client,
                        version: str, sha256: str):
    """Simulate the ESP32 OTA download+flash+reboot cycle."""
    toy.ota_in_progress = True
    logger.info(f"[{toy.device_id}] OTA started → version {version}")

    # Simulate download time proportional to 1MB firmware
    download_secs = random.uniform(8, 15)
    await asyncio.sleep(download_secs)

    # 95% success rate; 5% SHA256 failure
    success = random.random() > 0.05

    if success:
        # Report success + new firmware version
        status = {
            "status": "online",
            "ota_status": "success",
            "fw_version": version,
            "battery_level": toy.battery,
            "wifi_signal": toy.wifi_rssi,
        }
        client.publish(toy.topic_status, json.dumps(status), qos=1)
        logger.info(f"[{toy.device_id}] OTA complete → now on {version}")
        # Simulate reboot delay
        await asyncio.sleep(3)
        toy.ota_in_progress = False
    else:
        status = {
            "status": "online",
            "ota_status": "failed",
            "fw_version": "unknown",
        }
        client.publish(toy.topic_status, json.dumps(status), qos=1)
        logger.warning(f"[{toy.device_id}] OTA FAILED — SHA256 mismatch (simulated)")
        toy.ota_in_progress = False


# ─── Toy lifecycle ────────────────────────────────────────────────────────────

async def run_toy(toy: ToyState, auto: bool, interval: float,
                  questions_override: Optional[list] = None):
    on_connect, on_disconnect, on_message = make_callbacks(toy)

    client = gmqtt.Client(toy.device_id)
    client.set_auth_credentials(toy.device_id, toy.api_key)
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message
    toy.client = client

    connect_kwargs = {}
    if toy.use_tls:
        import ssl
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE  # dev-only: skip cert verify
        connect_kwargs["ssl"] = ssl_ctx

    logger.info(f"[{toy.device_id}] Connecting to {toy.broker}:{toy.port}")
    await client.connect(toy.broker, toy.port, keepalive=60, **connect_kwargs)

    # Start heartbeat and question tasks concurrently
    await asyncio.gather(
        _heartbeat_loop(toy, client),
        _question_loop(toy, client, auto, interval, questions_override),
    )


async def _heartbeat_loop(toy: ToyState, client: gmqtt.Client):
    while True:
        if toy.connected and not toy.ota_in_progress:
            # Slowly drain battery
            toy.battery = max(5, toy.battery - random.randint(0, 1))
            toy.wifi_rssi = toy.wifi_rssi + random.randint(-3, 3)
            toy.wifi_rssi = max(-90, min(-30, toy.wifi_rssi))

            status = {
                "status": "online",
                "battery_level": toy.battery,
                "wifi_signal": toy.wifi_rssi,
                "fw_version": "1.0.0",
            }
            client.publish(toy.topic_status, json.dumps(status), qos=0)

        await asyncio.sleep(30)


async def _question_loop(toy: ToyState, client: gmqtt.Client,
                         auto: bool, interval: float,
                         questions_override: Optional[list]):
    q_pool = questions_override or QUESTIONS

    if auto:
        while True:
            # Simulate WiFi drop
            if toy.drop_rate > 0 and random.random() < toy.drop_rate:
                logger.warning(f"[{toy.device_id}] Simulated WiFi drop")
                await client.disconnect()
                await asyncio.sleep(random.uniform(3, 10))
                await client.reconnect()

            if toy.connected and not toy.ota_in_progress:
                question = random.choice(q_pool)
                await _send_question(toy, client, question)

            jitter = interval * random.uniform(0.8, 1.2)
            await asyncio.sleep(jitter)
    else:
        # Interactive mode
        loop = asyncio.get_event_loop()
        while True:
            question = await loop.run_in_executor(
                None, lambda: input(f"\n[{toy.device_id}] Ask: ")
            )
            if question.lower() in ("quit", "exit", "q"):
                break
            await _send_question(toy, client, question)


async def _send_question(toy: ToyState, client: gmqtt.Client, question: str):
    # Gateway expects {"text": "..."} — matches the firmware's audio/in envelope.
    payload = json.dumps({"text": question})
    toy._last_question_time = time.time()
    toy.questions_sent += 1
    client.publish(toy.topic_audio_in, payload, qos=1)
    logger.info(f"[{toy.device_id}] Q#{toy.questions_sent}: {question}")


# ─── Stats printer ────────────────────────────────────────────────────────────

async def stats_loop(toys: list[ToyState], interval: float = 15.0):
    while True:
        await asyncio.sleep(interval)
        print("\n" + "═" * 60)
        print(f"  STATS  ({len(toys)} toys)")
        print("═" * 60)
        total_q = sum(t.questions_sent for t in toys)
        total_r = sum(t.replies_received for t in toys)
        all_lat = [l for t in toys for l in t.latencies]
        avg_lat = sum(all_lat) / len(all_lat) if all_lat else 0
        print(f"  Questions sent   : {total_q}")
        print(f"  Replies received : {total_r}")
        print(f"  Answer rate      : {total_r/total_q*100:.1f}%" if total_q else "  Answer rate: N/A")
        print(f"  Avg latency      : {avg_lat:.2f}s")
        connected = sum(1 for t in toys if t.connected)
        print(f"  Connected toys   : {connected}/{len(toys)}")
        print("═" * 60)

# ─── Entry point ─────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Boboloo Toy Simulator")
    p.add_argument("--broker",    default="localhost", help="MQTT broker host")
    p.add_argument("--port",      type=int, default=1883, help="MQTT broker port")
    p.add_argument("--tls",       action="store_true", help="Enable TLS")
    p.add_argument("--device",    default=None, help="Single device ID (e.g. TEST001)")
    p.add_argument("--key",       default=None, help="Toy API key for single device")
    p.add_argument("--count",     type=int, default=1, help="Number of simulated toys")
    p.add_argument("--prefix",    default="SIM", help="Device ID prefix for multi-toy")
    p.add_argument("--auto",      action="store_true", help="Auto-send random questions")
    p.add_argument("--interval",  type=float, default=5.0, help="Seconds between questions (auto mode)")
    p.add_argument("--drop-rate", type=float, default=0.0, help="WiFi drop probability per question (0.0–1.0)")
    p.add_argument("--ota-test",  action="store_true", help="Simulate an OTA command after 10s")
    p.add_argument("--stats",     type=float, default=15.0, help="Stats print interval (seconds)")
    return p.parse_args()


async def main():
    args = parse_args()

    if args.device and args.key:
        toys = [ToyState(
            device_id=args.device.upper(),
            api_key=args.key,
            broker=args.broker,
            port=args.port,
            use_tls=args.tls,
            drop_rate=args.drop_rate,
        )]
    elif args.count > 1:
        if not args.key:
            logger.error("Multi-toy mode requires --key (all toys share one key for dev)")
            sys.exit(1)
        toys = [
            ToyState(
                device_id=f"{args.prefix}{i:03d}",
                api_key=args.key,
                broker=args.broker,
                port=args.port,
                use_tls=args.tls,
                drop_rate=args.drop_rate,
            )
            for i in range(1, args.count + 1)
        ]
    else:
        logger.error("Provide --device + --key for single toy, or --count + --key for multi-toy")
        sys.exit(1)

    tasks = [run_toy(t, args.auto, args.interval) for t in toys]

    if args.auto and len(toys) > 1:
        tasks.append(stats_loop(toys, args.stats))

    if args.ota_test:
        tasks.append(_inject_ota_after_delay(toys[0]))

    await asyncio.gather(*tasks)


async def _inject_ota_after_delay(toy: ToyState):
    await asyncio.sleep(10)
    logger.info(f"[{toy.device_id}] Injecting fake OTA command...")
    cmd = json.dumps({
        "type": "ota",
        "version": "1.1.0",
        "url": "http://localhost:9999/fake-firmware.bin",
        "sha256": "abc123" * 10,
        "size": 1048576,
    })
    if toy.client:
        toy.client.publish(toy.topic_cmd, cmd, qos=1)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
