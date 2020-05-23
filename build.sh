#!/bin/bash -ex

JOBS=8

FFMPEG_TAG=n4.2.1
LIBYUV_TAG=cbe5385055b9360cacd14877450631b87eea1fcd
LIBZVBI_TAG=e62d905e00cdd1d6d4333ead90fb5b44bfb49371
X265_TAG=Release_3.3
X265_TAG=ef1c5205fc14d436b71b1459eba0c85fec0013b7
JSONC_TAG=6c55f65d07a972dbd2d1668aab2e0056ccdd52fc
BUILD_X265=0
BUILD_LIBWEBSOCKETS=0
LIBWEBSOCKETS_TAG=v3.2.0
BUILD_JSONC=0
BUILD_LIBAV=1
BUILD_VAAPI=0
BUILD_NVENC=0
BUILD_LIBLTNTSTOOLS=0

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
	# 3.0.0
	sudo yum -y install openssl-devel
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
elif [ "$1" == "vid.obe.2.0.16" ]; then
	OBE_TAG=vid.obe.2.0.16
	LIBKLVANC_TAG=vid.obe.1.2.1
	LIBKLSCTE35_TAG=vid.obe.1.2.0
	LIBMPEGTS_TAG=hevc-dev
	BUILD_X265=1
elif [ "$1" == "vid.obe.2.0.22" ]; then
	OBE_TAG=vid.obe.2.0.22
	LIBKLVANC_TAG=vid.obe.1.2.2
	LIBKLSCTE35_TAG=vid.obe.1.2.0
	LIBMPEGTS_TAG=hevc-dev
	BUILD_X265=1
elif [ "$1" == "vid.obe.2.0" ]; then
	OBE_TAG=2.0.0
	LIBKLVANC_TAG=vid.obe.1.2.2
	LIBKLSCTE35_TAG=vid.obe.1.2.0
	LIBMPEGTS_TAG=hevc-dev
	BUILD_X265=1
elif [ "$1" == "vid.obe.3.0.0" ]; then
	OBE_TAG=3.0.0
	LIBKLVANC_TAG=vid.obe.1.2.2
	LIBKLSCTE35_TAG=vid.obe.1.2.0
	LIBMPEGTS_TAG=hevc-dev
	BUILD_X265=1
	BUILD_LIBAV=0
	BUILD_VAAPI=0
	BUILD_LIBWEBSOCKETS=0
	BUILD_JSONC=0
	BUILD_LIBLTNTSTOOLS=1
elif [ "$1" == "vid.obe.3.0.3" ]; then
	OBE_TAG=vid.obe.3.0.3
	LIBKLVANC_TAG=vid.obe.1.2.2
	LIBKLSCTE35_TAG=vid.obe.1.2.0
	LIBMPEGTS_TAG=hevc-dev
	BUILD_X265=1
	BUILD_LIBAV=0
	BUILD_VAAPI=0
	BUILD_LIBWEBSOCKETS=0
	BUILD_JSONC=0
elif [ "$1" == "vid.obe.3.1.0" ]; then
	OBE_TAG=vid.obe.3.1.0
	LIBKLVANC_TAG=vid.obe.1.2.2
	LIBKLSCTE35_TAG=vid.obe.1.2.0
	LIBMPEGTS_TAG=hevc-dev
	BUILD_X265=1
	BUILD_LIBAV=0
	BUILD_VAAPI=0
	BUILD_LIBWEBSOCKETS=0
	BUILD_JSONC=0
	BUILD_LIBLTNTSTOOLS=1
elif [ "$1" == "vid.obe.3.2.6" ]; then
	OBE_TAG=vid.obe.3.2.6
	LIBKLVANC_TAG=vid.obe.1.2.2
	LIBKLSCTE35_TAG=vid.obe.1.2.0
	LIBMPEGTS_TAG=hevc-dev
	BUILD_X265=1
	BUILD_LIBAV=0
	BUILD_VAAPI=0
	BUILD_LIBWEBSOCKETS=0
	BUILD_JSONC=0
	BUILD_LIBLTNTSTOOLS=1
elif [ "$1" == "vid.obe.3.2.7" ]; then
	OBE_TAG=vid.obe.3.2.7
	LIBKLVANC_TAG=vid.obe.1.2.2
	LIBKLSCTE35_TAG=vid.obe.1.2.0
	LIBMPEGTS_TAG=hevc-dev
	BUILD_X265=1
	BUILD_LIBAV=0
	BUILD_VAAPI=0
	BUILD_LIBWEBSOCKETS=0
	BUILD_JSONC=0
	BUILD_LIBLTNTSTOOLS=1
elif [ "$1" == "vid.obe.3.0-dev" ]; then
	OBE_TAG=3.0.0
	LIBKLVANC_TAG=vid.obe.1.2.2
	LIBKLSCTE35_TAG=vid.obe.1.2.0
	LIBMPEGTS_TAG=hevc-dev
	BUILD_X265=1
	BUILD_LIBAV=0
	BUILD_VAAPI=0
	BUILD_LIBWEBSOCKETS=0
	BUILD_JSONC=0
	BUILD_LIBLTNTSTOOLS=1
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

if [ $BUILD_LIBLTNTSTOOLS -eq 1 ]; then
	if [ ! -d libltntstools ]; then
		git clone https://github.com/LTNGlobal-opensource/libltntstools
	fi
fi

if [ $BUILD_JSONC -eq 1 ]; then
	if [ ! -d json-c ]; then
		git clone https://github.com/json-c/json-c.git
		cd json-c && git checkout $JSONC_TAG && cd ..
	fi
fi

if [ $BUILD_LIBWEBSOCKETS -eq 1 ]; then
	if [ ! -d libwebsockets ]; then
		git clone https://libwebsockets.org/repo/libwebsockets
		cd libwebsockets && git checkout $LIBWEBSOCKETS_TAG && cd ..
	fi
fi

if [ $BUILD_X265 -eq 1 ]; then
	if [ ! -d x265 ]; then
		git clone https://github.com/videolan/x265.git
		cd x265
		git checkout $X265_TAG
		patch -p1 <../0003-x265-sei-overflow.patch
		patch -p1 <../0004-x265-sei-additional-elements-segfault.patch
		#patch -p1 <../0005-x265-additional-recovery-points.patch
		cd ..
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
		cd libklvanc && git checkout $LIBKLVANC_TAG && cd ..
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

if [ $BUILD_LIBLTNTSTOOLS -eq 1 ]; then
	pushd libltntstools
		if [ ! -f .skip ]; then
			./autogen.sh --build
			./configure --prefix=$PWD/../target-root/usr/local --enable-shared=no
			make -j$JOBS
			make install
			touch .skip
		fi
	popd
fi

if [ $BUILD_VAAPI -eq 1 ]; then
	pushd vaapi
		./build.sh
		# Newer builds default to the iHD driver, use the haswell compatible driver.
		# LIBVA_DRIVER_NAME=i965 ./vainfo
	popd
fi

if [ $BUILD_NVENC -eq 1 ]; then
	# References:
	# https://linuxconfig.org/how-to-install-nvidia-cuda-toolkit-on-centos-7-linux
	# https://arstech.net/compile-ffmpeg-with-nvenc-h264/
	# TODO
	# Blacklist the nouveau driver
	# Install the proprietary NVidia driver
	# $ wget https://developer.download.nvidia.com/compute/cuda/repos/rhel7/x86_64/cuda-repo-rhel7-10.0.130-1.x86_64.rpm
	# $ sudo rpm -i cuda-repo-rhel7-10.0.130-1.x86_64.rpm
	# Install cuda and A LOT of deps
	# $ sudo yum -i install cuda
fi

if [ $BUILD_JSONC -eq 1 ]; then
	pushd json-c
		if [ ! -f .skip ]; then
			./autogen.sh
			./configure --prefix=$PWD/../target-root/usr/local --enable-shared=no
			make -j$JOBS
			make install
			touch .skip
		fi
	popd
fi

if [ $BUILD_LIBWEBSOCKETS -eq 1 ]; then
	pushd libwebsockets
		if [ ! -f .skip ]; then
			mkdir -p build
			cd build
			cmake -DCMAKE_INSTALL_PREFIX:PATH=$PWD/../../target-root/usr/local ..
			make -j$JOBS
			make install
			cd ..
			touch .skip
		fi
	popd
fi

if [ $BUILD_X265 -eq 1 ]; then
	pushd x265/build/linux
		if [ ! -f .skip ]; then
			# Static only build
			#./make-Makefiles.bash
			cmake -DCMAKE_INSTALL_PREFIX=$PWD/../../../target-root/usr/local \
				-G "Unix Makefiles" \
				-DENABLE_SHARED:bool=off \
				../../source
			make -j$JOBS
			make install
			#touch .skip
		fi
	popd
fi

pushd libzvbi
	if [ ! -f .skip ]; then
		./configure --enable-shared=no --prefix=$PWD/../target-root/usr/local
		make && make install
		make install
		touch .skip
	fi
popd

pushd libklvanc
	if [ ! -f .skip ]; then
		./autogen.sh --build
		./configure --enable-shared=no --prefix=$PWD/../target-root/usr/local --enable-dep-curses=no
		make && make install
		make install
		touch .skip
	fi
popd

pushd libklscte35
	if [ ! -f .skip ]; then
		./autogen.sh --build
		export CFLAGS="-I$PWD/../target-root/usr/local/include"
		export LDFLAGS="-L$PWD/../target-root/usr/local/lib"
		./configure --enable-shared=no --prefix=$PWD/../target-root/usr/local
		make && make install
		touch .skip
	fi
popd

pushd libmpegts-obe
	if [ ! -f .skip ]; then
		./configure --prefix=$PWD/../target-root/usr/local
		make && make install
		make install
		touch .skip
	fi
popd

pushd twolame-0.3.13
	if [ ! -f .skip ]; then
		./configure --prefix=$PWD/../target-root/usr/local --enable-shared=no
		make && make install
		touch .skip
	fi
popd

pushd x264-obe
	if [ ! -f .skip ]; then
		make clean
		./configure --enable-static --disable-cli --prefix=$PWD/../target-root/usr/local --disable-lavf --disable-swscale --disable-opencl
		make -j$JOBS && make install
		touch .skip
	fi
popd

pushd fdk-aac
	if [ ! -f .skip ]; then
		./autogen.sh
		./configure --prefix=$PWD/../target-root/usr/local --enable-shared=no
		make && make install
		touch .skip
	fi
popd

if [ $BUILD_LIBAV -eq 1 ]; then
	pushd libav-obe
		if [ ! -f .skip ]; then
			./configure --prefix=$PWD/../target-root/usr/local --enable-libfdk-aac --enable-gpl --enable-nonfree \
				--disable-swscale-alpha --disable-avdevice \
				--extra-ldflags="-L$PWD/../target-root/usr/local/lib" \
				--extra-cflags="-I$PWD/../target-root/usr/local/include -ldl"
			make -j$JOBS && make install
			touch .skip
		fi
	popd
else
	pushd ffmpeg
	if [ ! -f .skip ]; then
		export CFLAGS="-I$PWD/../target-root/usr/local/include -I$BMSDK_10_11_2/include"
		export LDFLAGS="-L$PWD/../target-root/usr/local/lib -lpthread -ldl"
		export PKG_CONFIG_PATH=$PWD/../target-root/usr/local/lib/pkgconfig:/usr/local/lib/pkgconfig
		./configure --prefix=$PWD/../target-root/usr/local \
			--enable-libfreetype \
			--enable-gpl \
			--enable-nonfree \
			--enable-decklink \
			--enable-libfdk-aac \
			--disable-swscale-alpha \
			--enable-libfdk-aac \
			--enable-libx264 \
			--pkg-config-flags="--static" \
			--enable-libx265 \
			--enable-gpl \
			--enable-nonfree

# For NVENC and Cuda
#			--enable-cuda-nvcc --enable-cuvid --enable-nvenc \
#			--extra-cflags="-I/usr/local/cuda/include/" \
#			--extra-ldflags=-L/usr/local/cuda/lib64/

		make -j$JOBS && make install
		unset CFLAGS
		unset LDFLAGS
		unset PKG_CONFIG_PATH
		touch .skip
	fi
	popd
fi

pushd libyuv
	if [ ! -f .skip ]; then
		make -f linux.mk
		cp -r include/* $PWD/../target-root/usr/local/include
		cp libyuv.a $PWD/../target-root/usr/local/lib
		touch .skip
	fi
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
