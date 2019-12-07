#!/bin/bash -ex

JOBS=8

FFMPEG_TAG=n4.2.1
LIBYUV_TAG=cbe5385055b9360cacd14877450631b87eea1fcd
LIBZVBI_TAG=e62d905e00cdd1d6d4333ead90fb5b44bfb49371
X265_TAG=95d81a19c92f0b37b292ff2f7e5192806546f1dd
BUILD_X265=0
BUILD_LIBWEBSOCKETS=0
LIBWEBSOCKETS_TAG=master
#BUILD_JSONC=1
BUILD_LIBAV=1
BUILD_VAAPI=0

# https://github.com/json-c/json-c.git
# cd json-c
# ./autogen.sh
# ./configure --prefix=$PWD/../target-root/usr/local --enable-shared=no
# make
# make install

# https://github.com/libjpeg-turbo/libjpeg-turbo.git
# cd libjpeg-turbo
# mkdir build
# cd build
# cmake -G"Unix Makefiles" -DCMAKE_INSTALL_PREFIX:PATH=$PWD/../../target-root/usr/local ..
# make
# make install


if [ "$1" == "" ]; then
	# Fine if they do not specify a tag
	echo "No specific tag specified.  Using master"
	OBE_TAG=master
elif [ "$1" == "--installdeps" ]; then
	# We need epel for YASM
	sudo yum -y install epel-release
	sudo yum -y install yum-utils
	sudo yum-config-manager --add-repo http://www.nasm.us/nasm.repo
	sudo yum -y install nasm
	sudo yum repolist
	sudo yum -y install libtool
	sudo yum -y install libpng
	sudo yum -y install yasm
	sudo yum -y install cmake
	sudo yum -y install perl-CPAN
	sudo yum -y install perl-Digest-MD5
	sudo yum -y install zlib-devel
	sudo yum -y install bzip2-devel
	sudo yum -y install readline-devel
	sudo yum -y install ncurses-static
	sudo yum -y install readline-static
	sudo yum -y install alsa-lib-devel
	sudo yum -y install pulseaudio-libs-devel
	sudo yum -y install libpciaccess-devel
	#sudo perl -MCPAN -e 'install Digest::Perl::MD5'
	exit 0
elif [ "$1" == "experimental" ]; then
	OBE_TAG=experimental
elif [ "$1" == "customerd" ]; then
	OBE_TAG=customerd-0.1
	LIBKLVANC_TAG=customerd-0.1
	LIBKLSCTE35_TAG=customerd-0.1
elif [ "$1" == "104_refactoring" ]; then
	OBE_TAG=104_refactoring
	LIBKLVANC_TAG=104_refactoring
	LIBKLSCTE35_TAG=104_refactoring
elif [ "$1" == "vid.obe.1.1" ]; then
	OBE_TAG=vid.obe.1.1.20
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=vid.libmpegts-obe-1.1.2
elif [ "$1" == "vid.obe.2.0.11" ]; then
	OBE_TAG=vid.obe.2.0.11
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.2.0
	LIBMPEGTS_TAG=hevc-dev
	BUILD_X265=1
elif [ "$1" == "vid.obe.2.0.12" ]; then
	OBE_TAG=vid.obe.2.0.12
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=hevc-dev
	BUILD_X265=1
elif [ "$1" == "vid.obe.2.0.14" ]; then
	OBE_TAG=vid.obe.2.0.14
	LIBKLVANC_TAG=vid.obe.1.2.1
	LIBKLSCTE35_TAG=vid.obe.1.2.0
	LIBMPEGTS_TAG=hevc-dev
	BUILD_X265=1
elif [ "$1" == "vid.obe.2.0.15" ]; then
	OBE_TAG=vid.obe.2.0.15
	LIBKLVANC_TAG=vid.obe.1.2.1
	LIBKLSCTE35_TAG=vid.obe.1.2.0
	LIBMPEGTS_TAG=hevc-dev
	BUILD_X265=1
elif [ "$1" == "vid.obe.2.0" ]; then
	OBE_TAG=2.0.0
	LIBKLVANC_TAG=vid.obe.1.2.1
	LIBKLSCTE35_TAG=vid.obe.1.2.0
	LIBMPEGTS_TAG=hevc-dev
	BUILD_X265=1
elif [ "$1" == "vid.obe.3.0" ]; then
	OBE_TAG=3.0.0
	LIBKLVANC_TAG=vid.obe.1.2.1
	LIBKLSCTE35_TAG=vid.obe.1.2.0
	LIBMPEGTS_TAG=hevc-dev
	BUILD_X265=1
	BUILD_LIBAV=0
	BUILD_VAAPI=1
elif [ "$1" == "vid.obe.1.1.12" ]; then
	OBE_TAG=vid.obe.1.1.12
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=vid.libmpegts-obe-1.1.2
elif [ "$1" == "vid.obe.1.1.14" ]; then
	OBE_TAG=vid.obe.1.1.14
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=vid.libmpegts-obe-1.1.2
elif [ "$1" == "vid.obe.1.1.15" ]; then
	OBE_TAG=vid.obe.1.1.15
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=vid.libmpegts-obe-1.1.2
elif [ "$1" == "vid.obe.1.1.16" ]; then
	OBE_TAG=vid.obe.1.1.16
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=vid.libmpegts-obe-1.1.2
elif [ "$1" == "vid.obe.1.1.18" ]; then
	OBE_TAG=vid.obe.1.1.18
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=vid.libmpegts-obe-1.1.2
elif [ "$1" == "1.1.21" ]; then
	OBE_TAG=1.1.21
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=hevc-dev
else
	echo "Invalid argument"
	exit 1
fi
BMSDK_REPO=https://github.com/LTNGlobal-opensource/bmsdk.git

if [ ! -d bmsdk ]; then
    git clone $BMSDK_REPO
fi
if [ `uname -s` = "Darwin" ]; then
    PLAT=Mac
else
    PLAT=Linux
fi
BMSDK_10_11_2=$PWD/bmsdk/10.11.2/$PLAT
BMSDK_10_8_5=$PWD/bmsdk/10.8.5/$PLAT
BMSDK_10_1_1=$PWD/bmsdk/10.1.1/$PLAT

if [ $BUILD_LIBWEBSOCKETS -eq 1 ]; then
	if [ ! -d libwebsockets ]; then
		git clone https://libwebsockets.org/repo/libwebsockets
		cd libwebsockets && git checkout $LIBWEBSOCKETS_TAG && cd ..
	fi
fi

if [ $BUILD_X265 -eq 1 ]; then
	if [ ! -d x265 ]; then
		git clone https://github.com/videolan/x265.git
		cd x265 && git checkout $X265_TAG && cd ..
	fi
fi

if [ ! -d libzvbi ]; then
	git clone https://github.com/LTNGlobal-opensource/libzvbi.git
	if [ "$LIBZVBI_TAG" != "" ]; then
		cd libzvbi
		git checkout $LIBZVBI_TAG
		patch -p1 <../0000-libzvbi-remove-png-dep.patch
		cd ..
	fi
fi

if [ ! -d libklvanc ]; then
	git clone https://github.com/LTNGlobal-opensource/libklvanc.git
	if [ "$LIBKLVANC_TAG" != "" ]; then
		cd libklvanc
		git checkout $LIBKLVANC_TAG
		patch -p1 <../0001-libklvanc-remove-curses-dep.patch
		cd ..
	fi
fi

if [ ! -d libklscte35 ]; then
	git clone https://github.com/LTNGlobal-opensource/libklscte35.git
	if [ "$LIBKLSCTE35_TAG" != "" ]; then
		cd libklscte35 && git checkout $LIBKLSCTE35_TAG && cd ..
	fi
fi

if [ ! -d obe-rt ]; then
	git clone https://github.com/LTNGlobal-opensource/obe-rt.git
	if [ "$OBE_TAG" != "" ]; then
		cd obe-rt && git checkout $OBE_TAG && cd ..
	fi
fi

if [ ! -d x264-obe ]; then
	git clone https://github.com/LTNGlobal-opensource/x264-obe.git
fi

if [ ! -d fdk-aac ]; then
	git clone https://github.com/LTNGlobal-opensource/fdk-aac.git
fi

if [ $BUILD_LIBAV -eq 1 ]; then
	if [ ! -d libav-obe ]; then
		git clone https://github.com/LTNGlobal-opensource/libav-obe.git
	fi
else
	if [ ! -d ffmpeg ]; then
		git clone https://git.ffmpeg.org/ffmpeg.git ffmpeg
		cd ffmpeg && git checkout $FFMPEG_TAG && cd ..
	fi
fi

if [ ! -d libmpegts-obe ]; then
	git clone https://github.com/LTNGlobal-opensource/libmpegts-obe.git
	if [ "$LIBMPEGTS_TAG" != "" ]; then
		cd libmpegts-obe && git checkout $LIBMPEGTS_TAG && cd ..
	fi
fi

if [ ! -d libyuv ]; then
	git clone https://github.com/LTNGlobal-opensource/libyuv.git
	pushd libyuv
		# Make sure we use a known-good version
		git checkout $LIBYUV_TAG
		patch -p1 <../0002-libyuv-csc-height.patch
	popd
fi

if [ ! -d twolame-0.3.13 ]; then
	tar zxf twolame-0.3.13.tar.gz
fi

if [ $BUILD_VAAPI -eq 1 ]; then
	pushd vaapi
		./build.sh
		# Newer builds default to the iHD driver, use the haswell compatible driver.
		# LIBVA_DRIVER_NAME=i965 ./vainfo
	popd
fi

if [ $BUILD_LIBWEBSOCKETS -eq 1 ]; then
	pushd libwebsockets
		mkdir build
		cd build
		cmake -DCMAKE_INSTALL_PREFIX:PATH=$PWD/../../target-root/usr/local ..
		make -j$JOBS
		make install
	popd
fi

if [ $BUILD_X265 -eq 1 ]; then
	pushd x265/build/linux
		#./make-Makefiles.bash
		cmake -DCMAKE_INSTALL_PREFIX=$PWD/../../../target-root/usr/local -G "Unix Makefiles" ../../source
		make -j8
		make install
	popd
	# Remove these, we want a static link. TODO: X265 cfg build opt?
	rm -f target-root/usr/local/lib/libx265.so*
fi

pushd libzvbi
	./configure --enable-shared=no --prefix=$PWD/../target-root/usr/local
	make && make install
	make install
popd

pushd libklvanc
	./autogen.sh --build
	./configure --enable-shared=no --prefix=$PWD/../target-root/usr/local --enable-dep-curses=no
	make && make install
	make install
popd

pushd libklscte35
	./autogen.sh --build
	export CFLAGS="-I$PWD/../target-root/usr/local/include"
	export LDFLAGS="-L$PWD/../target-root/usr/local/lib"
	./configure --enable-shared=no --prefix=$PWD/../target-root/usr/local
	make && make install
popd

pushd libmpegts-obe
	./configure --prefix=$PWD/../target-root/usr/local
	make && make install
	make install
popd

pushd twolame-0.3.13
	./configure --prefix=$PWD/../target-root/usr/local --enable-shared=no
	make && make install
popd

pushd x264-obe
	make clean
	./configure --enable-static --disable-cli --prefix=$PWD/../target-root/usr/local --disable-lavf --disable-swscale --disable-opencl
	make -j$JOBS && make install
popd

pushd fdk-aac
	./autogen.sh
	./configure --prefix=$PWD/../target-root/usr/local --enable-shared=no
	make && make install
popd

if [ $BUILD_LIBAV -eq 1 ]; then
	pushd libav-obe
	./configure --prefix=$PWD/../target-root/usr/local --enable-libfdk-aac --enable-gpl --enable-nonfree \
		--disable-swscale-alpha --disable-avdevice \
		--extra-ldflags="-L$PWD/../target-root/usr/local/lib" \
		--extra-cflags="-I$PWD/../target-root/usr/local/include -ldl"
	make -j$JOBS && make install
	popd
else
	pushd ffmpeg
	export CFLAGS="-I$PWD/../target-root/usr/local/include -I$BMSDK_10_8_5/include"
	export LDFLAGS="-L$PWD/../target-root/usr/local/lib"
	export PKG_CONFIG_PATH=$PWD/../target-root/usr/local/lib/pkgconfig
	./configure --prefix=$PWD/../target-root/usr/local --enable-gpl --enable-nonfree --enable-libfdk-aac \
		--disable-swscale-alpha \
		--extra-ldflags="-L$PWD/../target-root/usr/local/lib" \
		--extra-cflags="-I$PWD/../target-root/usr/local/include -ldl"
	make -j$JOBS && make install
	unset CFLAGS
	unset LDFLAGS
	unset PKG_CONFIG_PATH
	popd
fi

pushd libyuv
	make -f linux.mk
	cp -r include/* $PWD/../target-root/usr/local/include
	cp libyuv.a $PWD/../target-root/usr/local/lib
popd

build_obe() {
    GITVER=`echo $1 | sed 's/^vid.obe.//'`
    BMSDK_DIR=$2
    BMVERSION=`cat $BMSDK_DIR/include/DeckLinkAPIVersion.h | grep BLACKMAGIC_DECKLINK_API_VERSION_STRING | awk '{print $3}'|sed -e 's/^"//' -e 's/"$//'`

    echo "Building OBE $GITVER for BlackMagic SDK version $BMVERSION"

    if [ "$1" == "customerd" ]; then
	pushd obe-rt
	export CXXFLAGS="-I$PWD/../target-root/usr/local/include -ldl"
	export PKG_CONFIG_PATH=$PWD/../target-root/usr/local/lib/pkgconfig
	./configure \
		--extra-ldflags="-L$PWD/../target-root/usr/local/lib -lfdk-aac -lavutil -lasound -lyuv -lklvanc" \
		--extra-cflags="-I$PWD/../target-root/usr/local/include -ldl" \
		--extra-cxxflags="-I$BMSDK_DIR"
	make clean
	make
	DESTDIR=$PWD/../target-root make install
	popd
    else
	pushd obe-rt
	export CFLAGS="-I$PWD/../target-root/usr/local/include -I$BMSDK_DIR"
	export LDFLAGS="-L$PWD/../target-root/usr/local/lib"
	export PKG_CONFIG_PATH=$PWD/../target-root/usr/local/lib/pkgconfig
	if [ -f autogen.sh ]; then
		./autogen.sh --build
	fi
	./configure --prefix=$PWD/../target-root/usr/local
	make clean
	make -j$JOBS
	make install
	popd
	cp target-root/usr/local/bin/obecli obecli-$GITVER-bm$BMVERSION
    fi
}

#build_obe $OBE_TAG $BMSDK_10_1_1
build_obe $OBE_TAG $BMSDK_10_8_5
