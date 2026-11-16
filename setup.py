"""Compatibility shim for editable installs with older pip frontends."""

from setuptools import find_packages, setup

setup(
    name="three-body-planet",
    version="0.2.0",
    description="Reproducible four-body initial-state generation, screening, and IAS15 evolution",
    package_dir={"": "src"},
    packages=find_packages("src"),
    package_data={"threebody": ["resources/presets/*.yaml"]},
    include_package_data=True,
    python_requires=">=3.11",
    install_requires=["numpy>=1.24", "pydantic>=2.5", "PyYAML>=6.0"],
    extras_require={
        "dev": ["pytest>=7.4", "ruff>=0.4", "rebound>=4.4,<5"],
        "physics": ["rebound>=4.4,<5"],
        "viz": ["plotly>=5.18,<7"],
    },
    entry_points={"console_scripts": ["threebody=threebody.cli:main"]},
)
