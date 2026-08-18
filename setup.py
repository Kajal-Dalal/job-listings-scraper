"""
Setup configuration for the Job Listings Scraper package.
"""
from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as f:
    requirements = [
        line.strip()
        for line in f
        if line.strip() and not line.startswith("#")
    ]

setup(
    name="job-listings-scraper",
    version="1.0.0",
    author="Kajal",
    author_email="kajal@example.com",
    description="Production-grade async job listings scraper with anti-detection and REST API",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/example/job-listings-scraper",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Internet :: WWW/HTTP :: Indexing/Search",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Framework :: AsyncIO",
    ],
    python_requires=">=3.11",
    install_requires=requirements,
    extras_require={
        "dev": [
            "pytest==8.3.3",
            "pytest-asyncio==0.24.0",
            "pytest-httpx==0.32.0",
            "black==24.10.0",
            "isort==5.13.2",
            "mypy==1.11.2",
        ]
    },
    entry_points={
        "console_scripts": [
            "job-scraper=src.main:run",
        ],
    },
)
