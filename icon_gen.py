#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
今日list 图标生成器（可重复运行）
设计语言：暖近黑圆角瓷砖 + 三条列表线（左）+ 青绿对勾圆（右）
配色取自 task_widget.py 设计令牌
"""
from PIL import Image, ImageDraw

S = 1024  # 超采样主画布
RAD = 224  # 圆角半径（256 下 56）

# ---- 设计令牌 ----
COL_BG_TOP  = (27, 31, 38, 255)    # #1b1f26
COL_BG_BOT  = (20, 23, 28, 255)    # #14171c
COL_BORDER  = (44, 52, 66, 255)    # #2c3442
COL_BAR_LO  = (92, 101, 114, 255)  # #5c6572
COL_BAR_MID = (154, 163, 178, 255) # #9aa3b2
COL_BAR_HI  = (237, 239, 243, 255) # #edeff3
COL_ACCENT  = (47, 168, 160, 255)  # #2fa8a0
COL_WHITE   = (255, 255, 255, 255)

def lerp(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(4))

def build_master():
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    # 1) 纵向渐变底
    grad = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    dg = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / (S - 1)
        dg.line([(0, y), (S, y)], fill=lerp(COL_BG_TOP, COL_BG_BOT, t))
    # 2) 圆角遮罩
    mask = Image.new('L', (S, S), 0)
    dm = ImageDraw.Draw(mask)
    dm.rounded_rectangle([0, 0, S - 1, S - 1], radius=RAD, fill=255)
    img.paste(grad, (0, 0), mask)
    # 3) 描边（外缘 1px 内缩，细亮边）
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([14, 14, S - 15, S - 15], radius=RAD - 14, outline=COL_BORDER, width=6)

    # 4) 三条列表线（左侧）
    def bar(x0, y, w, h, color):
        d.rounded_rectangle([x0, y, x0 + w, y + h], radius=h // 2, fill=color)

    bar(208, 336, 208, 52, COL_BAR_LO)   # 上：弱
    bar(208, 512, 264, 52, COL_BAR_HI)   # 中：亮（当前项）
    bar(208, 688, 160, 52, COL_BAR_MID)  # 下：中

    # 5) 右侧对勾圆（品牌青绿）
    cx, cy, cr = 712, 540, 148
    d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=COL_ACCENT)
    # 白色对勾（圆头圆角）
    pts = [(648, 540), (694, 588), (790, 476)]
    d.line(pts, fill=COL_WHITE, width=52, joint='curve')
    # 圆头端点
    for (px, py) in (pts[0], pts[-1]):
        d.ellipse([px - 26, py - 26, px + 26, py + 26], fill=COL_WHITE)
    return img

def main():
    master = build_master()
    # 预览大图
    master.resize((512, 512), Image.LANCZOS).save(r'D:\task_widget\icon_preview.png')
    # 多尺寸 .ico（PIL 直接由主图缩略生成全部尺寸）
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icon256 = master.resize((256, 256), Image.LANCZOS)
    icon256.save(r'D:\task_widget\icon.ico', format='ICO', sizes=sizes)
    print('icon.ico 已生成，包含尺寸:', sizes)

if __name__ == '__main__':
    main()
