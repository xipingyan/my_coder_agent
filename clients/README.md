# README

Based on some python scripts, test OVMS and other servers.

#### Test OVMS

```
source ovms_env/bin/activate
pip install openai

export no_proxy="${no_proxy},your_ip"
export NO_PROXY="${NO_PROXY},your_ip"

python test_ovms.py image1.jpg image2.jpg -p "对比这两张图的差异"

python test_ovms.py ../../release_gemma4/GoldenGate.png -p ./prompt.txt
```
