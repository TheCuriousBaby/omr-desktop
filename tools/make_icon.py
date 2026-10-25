"""生成程序图标 assets/icon.ico：蓝色圆角底板上的答题卡气泡网格。"""
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 512


def build():
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((16, 16, SIZE - 16, SIZE - 16), radius=96, fill=(47, 111, 237, 255))
    sheet = (86, 70, SIZE - 86, SIZE - 70)
    draw.rounded_rectangle(sheet, radius=28, fill=(255, 255, 255, 255))
    filled = {(0, 1), (1, 2), (2, 0), (3, 3)}
    left, top = sheet[0] + 52, sheet[1] + 50
    for row in range(4):
        for col in range(4):
            x, y = left + col * 70, top + row * 76
            box = (x, y, x + 46, y + 46)
            if (row, col) in filled:
                draw.ellipse(box, fill=(31, 41, 55, 255))
            else:
                draw.ellipse(box, outline=(148, 163, 184, 255), width=5)
    return image


if __name__ == "__main__":
    target = Path(__file__).resolve().parent.parent / "assets"
    target.mkdir(exist_ok=True)
    icon = build()
    icon.save(target / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icon.resize((256, 256)).save(target / "icon.png")
