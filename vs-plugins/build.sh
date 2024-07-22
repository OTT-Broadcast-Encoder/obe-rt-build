JOBS=8
if [ "$1" == "" ]; then
	echo "Building vapoursynth plugins..."
elif [ "$1" == "clean" ]; then
    rm -rf vapoursynth-mvtools vapoursynth-akarin
elif [ "$1" == "installdeps" ]; then
    apt install -y \
                llvm-15 \
                libfftw3-dev

	exit 0
fi

mkdir -p $HOME/.config/vapoursynth
mkdir -p plugins

echo "UserPluginDir=$PWD/plugins" >> $HOME/.config/vapoursynth/vapoursynth.conf

if [ ! -d vapoursynth-akarin ]; then
    git clone https://github.com/AkarinVS/vapoursynth-plugin.git vapoursynth-akarin
fi

if [ ! -d vapoursynth-mvtools ]; then
    git clone https://github.com/dubhater/vapoursynth-mvtools.git
fi

if [ ! -d vapoursynth-tcanny ]; then
    git clone https://github.com/HomeOfVapourSynthEvolution/VapourSynth-TCanny.git vapoursynth-tcanny
fi

if [ ! -d vapoursynth-bilateral_gpu ]; then
    # git clone https://github.com/Jaded-Encoding-Thaumaturgy/vapoursynth-BilateralGPU.git vapoursynth-bilateral_gpu
    git clone https://github.com/WolframRhodium/VapourSynth-BilateralGPU.git vapoursynth-bilateral_gpu
fi

if [ ! -d vapoursynth-noise ]; then
    git clone https://github.com/wwww-wwww/vs-noise.git vapoursynth-noise
fi

if [ ! -d vapoursynth-dfttest2 ]; then
    git clone --recurse-submodules https://github.com/AmusementClub/vs-dfttest2.git vapoursynth-dfttest2
fi


export CFLAGS="-I$PWD/../target-root/usr/local/include -I$PWD/../target-root/usr/local/include/vapoursynth"
export LDFLAGS="-L$PWD/../target-root/usr/local/lib"
export PKG_CONFIG_PATH=$PWD/../target-root/usr/local/lib/pkgconfig:/usr/local/lib/pkgconfig

pushd vapoursynth-akarin
	if [ ! -f .skip ]; then
        meson build
        ninja -C build
        cp build/libakarin.so ../plugins
        touch .skip
	fi
popd

pushd vapoursynth-mvtools
	if [ ! -f .skip ]; then
        ./autogen.sh
        ./configure --prefix=$PWD/../../target-root/usr/local
        make -j$JOBS
        cp -r .libs/* ../plugins
		touch .skip
	fi
popd

pushd vapoursynth-tcanny
	if [ ! -f .skip ]; then
        meson build
        ninja -C build
        cp build/libtcanny.so ../plugins
		touch .skip
	fi
popd

pushd vapoursynth-bilateral_gpu
	if [ ! -f .skip ]; then
        cmake -S . -B build -D CMAKE_BUILD_TYPE=Release \
            -D CMAKE_CUDA_FLAGS="--threads 0 --use_fast_math -Wno-deprecated-gpu-targets" \
            -D CMAKE_CUDA_ARCHITECTURES="50;61-real;75-real;86"
        cmake --build build --config Release
        cp build/rtc_source/libbilateralgpu_rtc.so ../plugins
        cp build/source/libbilateralgpu.so ../plugins
		touch .skip
	fi
popd

pushd vapoursynth-noise
	if [ ! -f .skip ]; then
        meson build
        ninja -C build
        cp build/libaddnoise.so ../plugins
		touch .skip
	fi
popd

pushd vapoursynth-dfttest2
	if [ ! -f .skip ]; then
        cmake -S . -B build -D ENABLE_CUDA=ON -D ENABLE_CPU=ON
        cmake --build build
        cp build/libdfttest2_cuda.so ../plugins
        cp build/libdfttest2_nvrtc.so ../plugins
        cp build/cpu_source/libdfttest2_cpu.so ../plugins
        cp dfttest2.py $(python3 -c "import typing; import pathlib; print(pathlib.Path(typing.__file__).parent)")
		touch .skip
	fi
popd