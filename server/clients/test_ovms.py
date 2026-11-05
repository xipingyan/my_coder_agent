import argparse
import base64
import io
import os
from openai import OpenAI
from PIL import Image


def encode_image_safely(image_path):
  """读取本地图片，统一转为标准 RGB JPEG 格式并编码为 Base64，

  彻底避免 PNG 透明通道、RGBA 或调色板格式导致 OVMS 后端解码成白图。
  """
  if not os.path.exists(image_path):
    raise FileNotFoundError(f"找不到图片文件: {image_path}")

  with Image.open(image_path) as img:
    print(
        f"[DEBUG] 原始图片: {image_path} | 格式: {img.format} | 模式:"
        f" {img.mode} | 尺寸: {img.size}"
    )
    # 如果是 RGBA, LA 或调色板模式 (P)，转换为纯 RGB
    if img.mode in ("RGBA", "LA", "P"):
      print("[DEBUG] 检测到非 RGB 颜色空间/透明通道，正在转换为 RGB...")
      img = img.convert("RGB")
    elif img.mode != "RGB":
      img = img.convert("RGB")

    # 存入内存字节流并转为 JPEG
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=95)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def load_prompt(prompt_arg):
  """如果 prompt_arg 是一个存在的文件路径，则读取文件内容作为 Prompt；

  否则直接将 prompt_arg 作为文本返回。
  """
  if os.path.isfile(prompt_arg):
    print(f"[+] 检测到提示词文件，正在读取: {prompt_arg}")
    with open(prompt_arg, "r", encoding="utf-8") as f:
      return f.read().strip()
  return prompt_arg


def main():
  parser = argparse.ArgumentParser(
      description=(
          "OVMS Qwen3.8-27B 视觉多模态测试脚本 (支持直接输入文字或读取提示词文件)"
      )
  )
  parser.add_argument("images", nargs="+", help="图片路径（可传多张）")
  parser.add_argument(
      "-p",
      "--prompt",
      default=(
          "描述一下图片中是否有车辆，如果有，查看车辆是否有损坏，描述损坏细节。"
      ),
      help="提示词文本，或者包含提示词内容的文本文件路径",
  )
  parser.add_argument(
      "--base-url",
      default="http://10.239.216.35:8000/v3",
      help="OVMS OpenAI 兼容接口地址",
  )
  parser.add_argument("--model", default="qwen3.8-27b", help="模型名称")

  args = parser.parse_args()

  # 智能加载 Prompt（支持纯文本或文件）
  prompt_text = load_prompt(args.prompt)

  client = OpenAI(api_key="ovms", base_url=args.base_url)

  content_list = [{"type": "text", "text": prompt_text}]

  print(f"[+] 正在安全处理 {len(args.images)} 张图片...")
  for idx, img_path in enumerate(args.images):
    b64_data = encode_image_safely(img_path)
    content_list.append({
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{b64_data}"},
    })
    print(f"    [{idx + 1}] 已转换为 RGB JPEG 并编码发送")

  print(f"[+] 正在向 OVMS 发送请求 (模型: {args.model})...")

  try:
    response = client.chat.completions.create(
        model=args.model,
        messages=[{"role": "user", "content": content_list}],
        max_tokens=1024,
        temperature=0.1,
    )

    print("\n================ 模型回复 ================")
    print(response.choices[0].message.content)
    print("==========================================")

  except Exception as e:
    print(f"\n[!] 请求失败: {e}")


if __name__ == "__main__":
  main()
