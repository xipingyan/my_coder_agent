# model download

ollama pull smtek/Swift-Qwen3.8-27B:map-k4v

下载后，copy到这里，ai帮我copy的。

copy script:
```
./copy_ollama_model.sh

<!-- 因为脚步启动使用的是map-k4v， 所以需要把latest copy一份map-k4v-->
cd manifests/registry.ollama.ai/smtek/Swift-Qwen3.8-27B
cp latest map-k4v
ls -la
```

注意事项：
ollama下载时，需要设置代理，还是ai给方案如何设置。