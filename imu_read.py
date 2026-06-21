#!/usr/bin/env python3
# HiPNUC HI12 IMU 串口二进制协议解码器 (0x91 数据包)
# 帧: 5A A5 | len(2,LE) | crc16(2,LE) | payload(len)
# payload(0x91): tag(1) id(1) rev(2) prs(4) ts(4,ms) acc[3] gyr[3] mag[3] eul[3] quat[4]  (float32 LE)
# CRC: CRC-16/XMODEM (poly 0x1021, init 0) over bytes[0:4] + payload
import os, sys, struct, time, math, statistics

DEV = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0

def crc16(data, crc=0):
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc

fd = os.open(DEV, os.O_RDONLY)
buf = bytearray()
t0 = time.time()
while time.time() - t0 < DUR:
    chunk = os.read(fd, 4096)
    if chunk:
        buf += chunk
os.close(fd)

frames, crc_ok, crc_bad = [], 0, 0
i = 0
while i < len(buf) - 6:
    if buf[i] == 0x5A and buf[i+1] == 0xA5:
        length = buf[i+2] | (buf[i+3] << 8)
        if i + 6 + length > len(buf):
            break
        crc_rx = buf[i+4] | (buf[i+5] << 8)
        c = crc16(buf[i:i+4]); c = crc16(buf[i+6:i+6+length], c)
        payload = buf[i+6:i+6+length]
        if c == crc_rx:
            crc_ok += 1
            if payload and payload[0] == 0x91 and length >= 76:
                ts   = struct.unpack_from('<I', payload, 8)[0]
                acc  = struct.unpack_from('<3f', payload, 12)
                gyr  = struct.unpack_from('<3f', payload, 24)
                mag  = struct.unpack_from('<3f', payload, 36)
                eul  = struct.unpack_from('<3f', payload, 48)
                quat = struct.unpack_from('<4f', payload, 60)
                frames.append((ts, acc, gyr, mag, eul, quat))
        else:
            crc_bad += 1
        i += 6 + length
    else:
        i += 1

print(f"读取字节: {len(buf)}  |  解析帧: {crc_ok+crc_bad}  |  CRC通过: {crc_ok}  CRC失败: {crc_bad}")
if not frames:
    print("!! 未解出有效 0x91 数据帧"); sys.exit(1)

dt = (frames[-1][0] - frames[0][0]) / 1000.0
rate = (len(frames) - 1) / dt if dt > 0 else 0
print(f"有效0x91帧: {len(frames)}  |  时间跨度: {dt:.3f}s  |  实测速率: {rate:.1f} Hz")
print("=" * 72)
for k in list(range(min(3, len(frames)))) + [len(frames) - 1]:
    ts, acc, gyr, mag, eul, quat = frames[k]
    amag = math.sqrt(sum(x*x for x in acc))
    tag = "首帧" if k < 3 else "末帧"
    print(f"[{tag} #{k}] t={ts}ms")
    print(f"  加速度(g):   X={acc[0]:+.3f}  Y={acc[1]:+.3f}  Z={acc[2]:+.3f}   |模|={amag:.3f}g")
    print(f"  角速度(deg/s): X={gyr[0]:+.2f}  Y={gyr[1]:+.2f}  Z={gyr[2]:+.2f}")
    print(f"  磁场(uT):    X={mag[0]:+.2f}  Y={mag[1]:+.2f}  Z={mag[2]:+.2f}")
    print(f"  欧拉角(deg): [0]={eul[0]:+.2f}  [1]={eul[1]:+.2f}  [2]={eul[2]:+.2f}")
    print(f"  四元数:      w={quat[0]:+.4f} x={quat[1]:+.4f} y={quat[2]:+.4f} z={quat[3]:+.4f}")

amags  = [math.sqrt(sum(x*x for x in f[1])) for f in frames]
gmax   = max(math.sqrt(sum(x*x for x in f[2])) for f in frames)
qnorms = [math.sqrt(sum(x*x for x in f[5])) for f in frames]
la = frames[-1][1]; lam = math.sqrt(sum(x*x for x in la))
pitch_acc = math.degrees(math.asin(max(-1, min(1, la[0]/lam))))
roll_acc  = math.degrees(math.atan2(-la[1], la[2]))
print("=" * 72)
print(f"加速度模 均值={statistics.mean(amags):.4f}g  (静止应≈1.000)")
print(f"角速度模 最大={gmax:.3f} deg/s  (静止应≈0)")
print(f"四元数模 均值={statistics.mean(qnorms):.4f}  (单位四元数应≈1.000)")
print(f"由加速度反算倾角: pitch≈{pitch_acc:+.2f}°, roll≈{roll_acc:+.2f}°  (应与欧拉角中对应分量吻合)")
