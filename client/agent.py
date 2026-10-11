"""Agent entry point: coordinates monitoring, queueing, and delivery.

The monitor runs in a background thread so HTTP timeouts never block the event
listener. The main thread drains the outbox and retries on a fixed interval.
"""

import argparse
import logging
import sys
import threading

from .config import ConfigError, load_config
from .monitor import LinuxUsbMonitor, make_synthetic_event
from .outbox import Outbox, utc_now_iso
from .sender import DeliveryOutcome, EventSender, HeartbeatSender

logger = logging.getLogger("client.agent")


class Agent:
    """Coordinates a device monitor, a persistent outbox, and an HTTP sender.

    When a ``heartbeat_sender`` is supplied, heartbeats are sent on their own
    thread so they never depend on (or block) USB event delivery.
    """

    def __init__(self, config, monitor, outbox, sender, heartbeat_sender=None):
        self.config = config
        self.monitor = monitor
        self.outbox = outbox
        self.sender = sender
        self.heartbeat_sender = heartbeat_sender
        self._stop_event = threading.Event()
        self._monitor_thread = None
        self._heartbeat_thread = None

    def enqueue_device_event(self, device_event):
        """Persist a monitor observation as a pending outbox event.

        Generates a unique event_id and server-compatible timestamp. Returns
        the event_id.
        """
        event_id = self.outbox.generate_event_id()
        self.outbox.enqueue(
            event_id=event_id,
            computer_id=self.config.computer_id,
            event_type=device_event.event_type,
            occurred_at=utc_now_iso(),
            device_category=device_event.device_category,
            vendor_id=device_event.vendor_id,
            product_id=device_event.product_id,
            device_name=device_event.device_name,
        )
        logger.info("Queued %s event %s (%s)",
                    device_event.event_type, event_id, device_event.device_name)
        return event_id

    def deliver_pending(self):
        """Try to deliver every pending event, in order.

        A failure on one event never blocks the others: transient and permanent
        failures are recorded but the loop continues. Returns the number of
        events successfully delivered.
        """
        delivered = 0
        for event in self.outbox.pending():
            if self._stop_event.is_set():
                break
            outcome, detail = self.sender.send(event)
            event_id = event["event_id"]
            if outcome == DeliveryOutcome.DELIVERED:
                self.outbox.mark_delivered(event_id)
                delivered += 1
                logger.info("Delivered event %s (%s)", event_id, detail)
            else:
                self.outbox.record_failure(event_id, detail)
                if outcome == DeliveryOutcome.AUTH_ERROR:
                    logger.warning("Authentication problem for event %s: %s",
                                   event_id, detail)
                else:
                    logger.info("Delivery failed for event %s (%s); will retry",
                                event_id, outcome)
        return delivered

    def _consume_monitor(self):
        try:
            for device_event in self.monitor.events():
                if self._stop_event.is_set():
                    break
                self.enqueue_device_event(device_event)
        except Exception:
            logger.exception("Monitor loop stopped unexpectedly")

    def send_heartbeat(self):
        """Send a single heartbeat, independently of USB activity.

        Returns the DeliveryOutcome, or None when no heartbeat sender is
        configured. Never raises: failures are logged and retried by the loop.
        """
        if self.heartbeat_sender is None:
            return None
        try:
            outcome, detail = self.heartbeat_sender.send(self.config.computer_id)
        except Exception:
            logger.exception("Heartbeat attempt failed unexpectedly")
            return DeliveryOutcome.TRANSIENT
        if outcome == DeliveryOutcome.DELIVERED:
            logger.info("Heartbeat delivered for %s (%s)",
                        self.config.computer_id, detail)
        else:
            logger.info("Heartbeat not delivered for %s (%s); will retry",
                        self.config.computer_id, outcome)
        return outcome

    def _heartbeat_loop(self):
        """Send heartbeats on a fixed interval until stopped."""
        while not self._stop_event.is_set():
            self.send_heartbeat()
            self._stop_event.wait(self.config.heartbeat_interval)

    def run(self):
        """Run until interrupted with Ctrl+C, then shut down cleanly."""
        self._monitor_thread = threading.Thread(
            target=self._consume_monitor, name="usb-monitor", daemon=True
        )
        self._monitor_thread.start()
        if self.heartbeat_sender is not None:
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop, name="heartbeat", daemon=True
            )
            self._heartbeat_thread.start()
        logger.info("Agent started for %s, sending to %s",
                    self.config.computer_id, self.config.events_url)

        try:
            while not self._stop_event.is_set():
                self.deliver_pending()
                # Wait for the retry interval; stop() interrupts this wait.
                self._stop_event.wait(self.config.retry_interval)
        except KeyboardInterrupt:
            logger.info("Interrupt received, shutting down")
        finally:
            self.stop()
            self._monitor_thread.join(timeout=5)
            if self._heartbeat_thread is not None:
                self._heartbeat_thread.join(timeout=5)
        logger.info("Agent stopped")

    def stop(self):
        """Signal threads to stop without corrupting the queue."""
        self._stop_event.set()
        try:
            self.monitor.stop()
        except Exception:
            pass


def build_agent(config, monitor):
    """Wire up a real Outbox, EventSender, and HeartbeatSender for the config."""
    outbox = Outbox(config.client_db_path)
    outbox.initialize()
    sender = EventSender(config.events_url, config.auth_token, config.request_timeout)
    heartbeat_sender = HeartbeatSender(
        config.heartbeat_url, config.auth_token, config.request_timeout
    )
    return Agent(
        config, monitor=monitor, outbox=outbox,
        sender=sender, heartbeat_sender=heartbeat_sender,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="USB monitoring client agent",
    )
    parser.add_argument(
        "--synthetic", action="store_true",
        help="Queue one clearly-marked synthetic test event instead of watching USB",
    )
    parser.add_argument(
        "--event-type", choices=["connected", "disconnected"], default="connected",
        help="Event type used together with --synthetic",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config()
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    if args.synthetic:
        # Synthetic mode: no USB access at all. The event is clearly labelled.
        from .monitor import SyntheticMonitor

        agent = build_agent(config, SyntheticMonitor([]))
        event = make_synthetic_event(args.event_type)
        logger.warning("SYNTHETIC event: this is a test, not a real USB detection")
        heartbeat_outcome = agent.send_heartbeat()
        agent.enqueue_device_event(event)
        delivered = agent.deliver_pending()
        remaining = agent.outbox.pending_count()
        print(f"Heartbeat: {heartbeat_outcome}")
        print(f"Delivered: {delivered}, still pending: {remaining}")
        return 0 if remaining == 0 else 1

    # Real monitoring path (Linux + pyudev only).
    agent = build_agent(config, LinuxUsbMonitor())
    agent.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
