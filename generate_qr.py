import qrcode


def generate_qr(factory_device_id: str):
    """
    Generates QR code for toy factory device ID
    """

    # Create QR
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_Q,
        box_size=10,
        border=4,
    )

    qr.add_data(factory_device_id)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    file_name = f"{factory_device_id}.png"

    img.save(file_name)

    print(f"✅ QR generated: {file_name}")


if __name__ == "__main__":

    factory_device_id = "BOBOLOO-TOY-003"

    generate_qr(factory_device_id)