from setuptools import setup, find_packages

setup(
    name="cloud-boost",
    version="3.0.0",
    description="Supercharge your cloud uploads — entropy-aware compression, quota-safe, auto-tuned rclone pipeline",
    author="Jay Maniya",
    packages=find_packages(),
    install_requires=[
        "click>=8.0.0",
        "pyyaml>=6.0",
        "colorama>=0.4.6",
    ],
    extras_require={
        "fast": ["zstandard>=0.21.0"],
    },
    entry_points={
        "console_scripts": [
            "cloudboost=cloudboost.cli:main",
        ],
    },
    python_requires=">=3.8",
)
