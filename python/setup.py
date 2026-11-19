from setuptools import setup, find_packages

setup(
    name="cloud-boost",
    version="1.0.0",
    description="Supercharge your cloud uploads with parallel chunking and compression",
    author="Jay Maniya",
    packages=find_packages(),
    install_requires=[
        "click>=8.0.0",
        "tqdm>=4.65.0",
        "pyyaml>=6.0",
        "requests>=2.31.0",
        "colorama>=0.4.6",
    ],
    entry_points={
        "console_scripts": [
            "cloudboost=cloudboost.cli:main",
        ],
    },
    python_requires=">=3.8",
)
