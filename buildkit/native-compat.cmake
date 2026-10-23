# PCL's locked binaries use baseline x86_64/Eigen allocation and C++14 alignment.
# Override upstream -march=native: Eigen's DEFAULT_ALIGN_BYTES can still become
# 64 with AVX512 despite MAX_ALIGN_BYTES=16, changing malloc/free across the ABI.
add_compile_options(-march=x86-64 -mtune=generic)
add_compile_definitions(EIGEN_MAX_ALIGN_BYTES=16 EIGEN_MAX_STATIC_ALIGN_BYTES=16 EIGEN_HAS_CXX17_OVERALIGN=0)
