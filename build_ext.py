"""Build the C++ implied-vol extension in place:  python build_ext.py build_ext --inplace   (needs a C++17 compiler)"""
from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import setup

setup(name="optmm-ext", ext_modules=[Pybind11Extension("optmm._iv", ["cpp/iv.cpp"], cxx_std=17, define_macros=[("_USE_MATH_DEFINES", "1")])], cmdclass={"build_ext": build_ext}, zip_safe=False)
