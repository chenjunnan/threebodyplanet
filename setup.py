"""Compatibility shim for editable installs with older pip frontends."""

from setuptools import find_packages, setup

setup(
    name="three-body-planet",
    version="0.1.0",
    description="Reproducible three-star/one-planet initial-state generator and screening engine",
    package_dir={"": "src"},
    packages=find_packages("src"),
    package_data={"threebody": ["resources/presets/*.yaml"]},
    include_package_data=True,
    python_requires=">=3.11",
    install_requires=["numpy>=1.24", "pydantic>=2.5", "PyYAML>=6.0"],
    extras_require={"dev": ["pytest>=7.4", "ruff>=0.4"]},
    entry_points={"console_scripts": ["threebody=threebody.cli:main"]},
)
