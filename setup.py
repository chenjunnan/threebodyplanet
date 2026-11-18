"""Compatibility shim for editable installs with older pip frontends."""

from setuptools import find_packages, setup

setup(
    name="three-body-planet",
    version="0.7.1",
    description="Reproducible three-star orbital, radiation, and thermal reference models",
    package_dir={"": "src"},
    packages=find_packages("src"),
    package_data={
        "threebody": [
            "resources/presets/*.yaml",
            "web/static/*.html",
            "web/static/*.js",
            "web/static/*.css",
            "web/static/*.json",
            "web/static/*.yaml",
        ]
    },
    include_package_data=True,
    python_requires=">=3.11",
    install_requires=[
        "numpy>=1.24,<3",
        "pydantic>=2.5,<3",
        "PyYAML>=6.0,<7",
        "scipy>=1.11,<2",
    ],
    extras_require={
        "dev": ["pytest>=7.4", "ruff>=0.4", "rebound>=4.4,<5"],
        "physics": ["rebound>=4.4,<5"],
        "viz": ["plotly>=5.18,<7"],
    },
    entry_points={"console_scripts": ["threebody=threebody.cli:main"]},
)
