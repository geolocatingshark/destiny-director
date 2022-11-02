from setuptools import setup

setup(
    name="sector_accounting",
    version="0.1.4",
    description="Discord Client Multiplexor with Discord Components support",
    url="https://github.com/gs729/sector_accounting",
    author="GS",
    author_email="geolocatingshark@gmail.com",
    license="GNU GPLv3",
    packages=["sector_accounting"],
    install_requires=["gspread", "pytz", "requests>=2.28.0"],
    zip_safe=False,
)
