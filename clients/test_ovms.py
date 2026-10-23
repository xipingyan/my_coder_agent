import argparse
import base64
import os
from openai import OpenAI


def encode_image(image_path):
  """将本地图片文件转换为 Base64 编码字符串"""
  if not os.path.exists(image_path):
    raise FileNotFoundError(f"找不到图片文件: {image_path}")
  with open(image_path, "rb") as image_file:
    return base64.b64encode(image_file.read()).decode("utf-8")


def main():
  parser = argparse.ArgumentParser(
      description=(
          "使用 OVMS 部署的 Qwen3.8-27B 多模态模型进行多图理解与描述测试"
      )
  )
  parser.add_argument(
      "images",
      nargs="+",
      help="要上传的一张或多张图片路径 (例如: image1.jpg image2.png)",
  )
  parser.add_argument(
      "-p",
      "--prompt",
      default="请详细描述这几张图片的内容，并分析它们之间的异同或关联。",
      help="给模型的提示词",
  )
  parser.add_argument(
      "--base-url",
      default="http://10.239.216.35:8000/v3",
      help="OVMS OpenAI 兼容接口地址",
  )
  parser.add_argument(
      "--model", default="qwen3.8-27b", help="OVMS 模型 ID"
  )

  args = parser.parse_args()

  # 初始化 OpenAI 客户端连接 OVMS (API Key 任意填写)
  client = OpenAI(api_key="ovms", base_url=args.base_url)

  # 构造多模态消息的 content 列表
  content_list = [{"type": "text", "text": args.prompt}]

  print(f"[+] 正在加载 {len(args.images)} 张图片...")
  for idx, img_path in enumerate(args.images):
    # 根据文件后缀自动匹配 MIME 类型
    ext = os.path.splitext(img_path)[1].lower()
    if ext == ".png":
      mime_type = "image/png"
    elif ext in [".webp"]:
      mime_type = "image/webp"
    else:
      mime_type = "image/jpeg"

    b64_data = encode_image(img_path)
    content_list.append({
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,{b64_data}"},
    })
    print(f"    [{idx + 1}] 已编码: {img_path}")

  print(f"[+] 正在向 OVMS 发送请求 (模型: {args.model})...")

  try:
    response = client.chat.completions.create(
        model=args.model,
        messages=[{"role": "user", "content": content_list}],
        max_tokens=1024,
        temperature=0.7,
    )

    print("\n================ 模型回复 ================")
    print(response.choices[0].message.content)
    print("==========================================")

  except Exception as e:
    print(f"\n[!] 请求失败: {e}")
    print(
        "提示: 请检查代理设置，确保当前终端已配置 no_proxy 包含 10.239.216.35"
    )
    print("      可执行: export no_proxy=$no_proxy,10.239.216.35")


if __name__ == "__main__":
  main()
