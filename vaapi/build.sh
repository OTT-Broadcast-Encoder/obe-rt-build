#!/bin/bash -ex

export PREFIX=$PWD/../target-root/usr/local
export LDFLAGS="-L$PREFIX/lib"
export PKG_CONFIG_PATH=$PREFIX/lib/pkgconfig
JOBS=${JOBS:-6}
LIBDRM_VERSION=2.4.134


if [ ! -d libva ]; then
	git clone --branch 2.24.1 --depth 1 https://github.com/intel/libva.git
fi

if [ ! -d libva-utils ]; then
	git clone --branch 2.24.0 --depth 1 https://github.com/intel/libva-utils.git
	pushd libva-utils/putsurface
		patch <../../01.patch
	popd
	pushd libva-utils
		patch -p1 <../02.patch
	popd
fi

if [ ! -d intel-vaapi-driver ]; then
	git clone https://github.com/intel/intel-vaapi-driver.git
	git -C intel-vaapi-driver checkout fd727a4e9cb8b2878a1e93d4dddc8dd1c1a4e0ea
fi

if [ ! -f libdrm-$LIBDRM_VERSION.tar.xz ]; then
	wget https://dri.freedesktop.org/libdrm/libdrm-$LIBDRM_VERSION.tar.xz
fi
printf '%s  %s\n' ac5e74d157830eb8bee44c6a6bf3ad49774ef0dd2a72bdad74a8f20308b52a95 \
	"libdrm-$LIBDRM_VERSION.tar.xz" | sha256sum -c -

if [ ! -d libdrm-$LIBDRM_VERSION ]; then
	tar xf libdrm-$LIBDRM_VERSION.tar.xz
fi


pushd libdrm-$LIBDRM_VERSION
	if [ ! -f .skip ]; then
		export CFLAGS="-I$PREFIX/include"
		export CXXFLAGS=$CFLAGS
		meson setup build --prefix="$PREFIX" --libdir=lib \
			-Dradeon=disabled -Damdgpu=disabled -Dnouveau=disabled \
			-Dvmwgfx=disabled -Dtests=false
		meson compile -C build -j"$JOBS"
		meson install -C build
		touch .skip
	fi
popd

pushd libva
	if [ ! -f .skip ]; then
		export CFLAGS="-I$PREFIX/include"
		export CXXFLAGS=$CFLAGS
		./autogen.sh
		./configure --prefix=$PREFIX --enable-va-messaging
		make -j"$JOBS"
		make install
		touch .skip
	fi
popd

pushd libva-utils
	if [ ! -f .skip ]; then
		export CFLAGS="-I$PREFIX/include --std=c99"
		export CXXFLAGS=$CFLAGS
		./autogen.sh
		./configure --prefix=$PREFIX
		make -j"$JOBS"
		make install
		touch .skip
	fi
popd


pushd intel-vaapi-driver
	if [ ! -f .skip ]; then
		export CFLAGS="-I$PREFIX/include"
		export CXXFLAGS=$CFLAGS
		./autogen.sh
		./configure --prefix=$PREFIX
		make -j"$JOBS"
		make install
		touch .skip
	fi
popd
