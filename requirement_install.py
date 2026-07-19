import subprocess
import sys

# 这里写你需要的依赖包
packages = ["openai", "pyyaml","chromadb"]

for package in packages:
    # 自动调用 pip 进行安装
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])
    print(f"✅ {package} 安装完成！")