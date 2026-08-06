from api.models.sensor import Device, RelayChannel

device = Device.objects.get(device_id="esp32_shed_3")

relay_config = [
    (1, 2, "Output 1", "normal"),
    (2, 4, "Output 2", "normal"),
    (3, 5, "Output 3", "normal"),
    (4, 13, "Output 4", "normal"),
    (5, 14, "Output 5", "normal"),
    (6, 16, "Output 6", "normal"),
    (7, 17, "Output 7", "normal"),
    (8, 18, "Output 8", "normal"),
    (9, 19, "1 HP Fan", "motor"),
    (10, 23, "1 HP Motor", "motor"),
    (11, 25, "Output 11", "normal"),
    (12, 26, "Output 12", "normal"),
    (13, 27, "Output 13", "normal"),
    (14, 15, "Output 14", "normal"),
    (15, 32, "Output 15", "normal"),
    (16, 0, "Output 16", "normal"),
]

for channel, gpio, name, load_type in relay_config:
    relay, created = RelayChannel.objects.update_or_create(
        device=device,
        channel_number=channel,
        defaults={
            "gpio_pin": gpio,
            "name": name,
            "load_type": load_type,
            "is_enabled": True,
            "desired_state": False,
            "actual_state": False,
            "commanded_at": None,
            "reported_at": None,
            "last_error": "",
        },
    )

    action = "Created" if created else "Updated"
    print(f"{action}: Channel {channel}, GPIO {gpio}, {name}")

print("All 16 relay channels configured safely as OFF.")