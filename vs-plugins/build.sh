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


export CFLAGS="-I$PWD/../target-root/usr/local/include"
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