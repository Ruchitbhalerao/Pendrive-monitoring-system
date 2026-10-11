"""USB device monitoring.

This module imports cleanly on any operating system. The Linux-only dependency
``pyudev`` is imported lazily inside :class:`LinuxUsbMonitor`, so tests and the
rest of the application run on macOS. A clear error is raised only when someone
actually tries to start real Linux monitoring on an unsupported platform.

Design notes:
* Device classification works on plain metadata dictionaries, so it is fully
  unit-testable without udev.
* The monitor never mounts, opens, reads, or copies files from a USB device.
  It only inspects device metadata reported by udev.
"""

import sys
from typing import Optional

STORAGE_CLASSES = {"08"}  # USB Mass Storage interface class
PERIPHERAL_CLASSES = {
    "01",  # Audio
    "03",  # Human Interface Device (keyboard, mouse)
    "06",  # Image / Still imaging
    "07",  # Printer
    "0e",  # Video
    "0a",  # CDC Communications
    "0f",  # Personal Healthcare
    "10",  # Audio/Video
    "11",  # Billboard
    "12",  # USB Type-C Bridge
    "13",  # USB Bulk Display
}
STORAGE_DRIVERS = {"usb-storage", "uas"}
PERIPHERAL_DRIVERS = {
    "usbhid", "hid", "usb", "snd-usb-audio", "snd_usb_audio",
    "cdc_acm", "usbserial", "rtl8153", "usbvision",
}

# Udev properties worth reading. These are metadata only, never file contents.
_INTERESTING_KEYS = (
    "DEVPATH",
    "ID_VENDOR_ID",
    "ID_MODEL_ID",
    "ID_VENDOR",
    "ID_MODEL",
    "ID_VENDOR_FROM_DATABASE",
    "ID_MODEL_FROM_DATABASE",
    "ID_USB_DRIVER",
    "ID_USB_INTERFACES",
    "ID_USB_CLASS_FROM_DATABASE",
)


class DeviceEvent:
    """A single USB connection or disconnection observation."""

    def __init__(self, event_type, device_category=None, vendor_id=None,
                 product_id=None, device_name=None):
        self.event_type = event_type
        self.device_category = device_category
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.device_name = device_name

    def as_dict(self):
        return {
            "event_type": self.event_type,
            "device_category": self.device_category,
            "vendor_id": self.vendor_id,
            "product_id": self.product_id,
            "device_name": self.device_name,
        }

    def __repr__(self):
        return (f"DeviceEvent({self.event_type!r}, {self.device_category!r}, "
                f"{self.vendor_id!r}, {self.product_id!r}, {self.device_name!r})")


def _parse_interface_classes(raw):
    """Extract interface class codes from ID_USB_INTERFACES (e.g. ':080650:')."""
    if not raw:
        return set()
    classes = set()
    for chunk in str(raw).split(":"):
        chunk = chunk.strip()
        if len(chunk) >= 2:
            classes.add(chunk[:2].lower())
    return classes


def _clean_text(value, max_len):
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    return text[:max_len]


def _device_name(properties):
    """Build a safe descriptive name from metadata, or None if unavailable."""
    candidates = (
        properties.get("ID_MODEL_FROM_DATABASE"),
        properties.get("ID_MODEL"),
        properties.get("ID_VENDOR_FROM_DATABASE"),
        properties.get("ID_VENDOR"),
    )
    for candidate in candidates:
        text = _clean_text(candidate, 256)
        if text:
            return text.replace("_", " ")
    return None


def classify_device(properties):
    """Classify a device as usb_storage, usb_peripheral, or unknown.

    Never assumes a device is storage. Returns 'unknown' when metadata is
    insufficient to decide.
    """
    properties = properties or {}
    driver = str(properties.get("ID_USB_DRIVER") or "").lower()
    classes = _parse_interface_classes(properties.get("ID_USB_INTERFACES"))
    db_class = str(properties.get("ID_USB_CLASS_FROM_DATABASE") or "").lower()

    if driver in STORAGE_DRIVERS or "08" in classes or "mass storage" in db_class:
        return "usb_storage"

    peripheral_hint = any(
        word in db_class
        for word in ("audio", "human interface", "keyboard", "mouse",
                     "printer", "video", "imaging", "wireless", "hub", "network")
    )
    if classes & PERIPHERAL_CLASSES or driver in PERIPHERAL_DRIVERS or peripheral_hint:
        return "usb_peripheral"

    return "unknown"


def device_event_from_properties(action, properties):
    """Build a DeviceEvent from a udev action and metadata dict.

    Returns None for actions that are not connect/disconnect.
    """
    if action == "add":
        event_type = "connected"
    elif action == "remove":
        event_type = "disconnected"
    else:
        return None

    properties = properties or {}
    return DeviceEvent(
        event_type=event_type,
        device_category=classify_device(properties),
        vendor_id=_clean_text(properties.get("ID_VENDOR_ID"), 32),
        product_id=_clean_text(properties.get("ID_MODEL_ID"), 32),
        device_name=_device_name(properties),
    )


class LinuxUsbMonitor:
    """Monitor real USB device add/remove events using pyudev.

    Only instantiate this on Linux. On other platforms (or when pyudev is not
    installed) the constructor raises a clear RuntimeError.
    """

    subsystem = "usb"

    def __init__(self):
        if sys.platform != "linux":
            raise RuntimeError(
                "Real USB monitoring requires Linux with udev; "
                f"the current platform is {sys.platform!r}. "
                "Use SyntheticMonitor or a fake monitor on this machine."
            )
        try:
            import pyudev  # noqa: F401  (Linux-only, imported lazily)
        except ImportError as error:
            raise RuntimeError(
                "pyudev is required for USB monitoring on Linux. "
                "Install it with: pip install pyudev"
            ) from error

        self._pyudev = pyudev
        self._stopped = False
        self._monitor = None
        self._seen_devpaths = set()

    def events(self):
        """Yield DeviceEvent objects for USB connect/disconnect events."""
        pyudev = self._pyudev
        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by(subsystem=self.subsystem)
        monitor.start()
        self._monitor = monitor

        while not self._stopped:
            device = monitor.poll(timeout=1)
            if device is None:
                continue
            if device.action not in ("add", "remove"):
                continue
            # Only react to the whole USB device, not each interface, so a
            # single physical device does not produce several events.
            if getattr(device, "device_type", None) != "usb_device":
                continue

            properties = {}
            for key in _INTERESTING_KEYS:
                try:
                    value = device.get(key)
                except Exception:
                    value = None
                if value is not None:
                    properties[key] = value

            event = device_event_from_properties(device.action, properties)
            if event is None:
                continue

            devpath = properties.get("DEVPATH") or getattr(device, "sys_path", None)
            if device.action == "add":
                if devpath in self._seen_devpaths:
                    continue
                self._seen_devpaths.add(devpath)
            else:
                self._seen_devpaths.discard(devpath)

            yield event

    def stop(self):
        """Request a clean stop; the events() generator exits shortly after."""
        self._stopped = True
        if self._monitor is not None:
            try:
                self._monitor.stop()
            except Exception:
                pass


class SyntheticMonitor:
    """A fake monitor that replays a fixed list of DeviceEvent objects.

    Used for development and tests, and to simulate events without hardware.
    """

    def __init__(self, events=None):
        self._events = list(events or [])
        self._stopped = False

    def events(self):
        for event in self._events:
            if self._stopped:
                return
            yield event

    def stop(self):
        self._stopped = True


def make_synthetic_event(event_type="connected", device_category="usb_storage",
                         vendor_id="0000", product_id="0000",
                         device_name="TEST DEVICE"):
    """Create a clearly-marked synthetic test event (not a real detection)."""
    if not device_name.startswith("[SYNTHETIC-TEST]"):
        device_name = f"[SYNTHETIC-TEST] {device_name}"
    return DeviceEvent(
        event_type=event_type,
        device_category=device_category,
        vendor_id=vendor_id,
        product_id=product_id,
        device_name=device_name,
    )
