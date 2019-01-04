#!/bin/bash -ex

JOBS=8

LIBZVBI_TAG=e62d905e00cdd1d6d4333ead90fb5b44bfb49371
X265_TAG=95d81a19c92f0b37b292ff2f7e5192806546f1dd
BUILD_X265=0

if [ "$1" == "" ]; then
	# Fine if they do not specify a tag
	echo "No specific tag specified.  Using master"
	OBE_TAG=master
elif [ "$1" == "--installdeps" ]; then
	# We need epel for YASM
	sudo yum -y install epel-release
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
	sudo perl -MCPAN -e 'install Digest::Perl::MD5'
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
elif [ "$1" == "vid.obe.2.0" ]; then
	OBE_TAG=vid.obe.2.0.5
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=hevc-dev
	BUILD_X265=1
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
elif [ "$1" == "52workaround" ]; then
	OBE_TAG=52workaround
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=vid.libmpegts-obe-1.1.2
elif [ "$1" == "audio-offset" ]; then
	OBE_TAG=audio-offset
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=vid.libmpegts-obe-1.1.2
elif [ "$1" == "syslog-string" ]; then
	OBE_TAG=syslog-string
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=vid.libmpegts-obe-1.1.2
elif [ "$1" == "ac3crc" ]; then
	OBE_TAG=ac3crc
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=vid.libmpegts-obe-1.1.2
elif [ "$1" == "1080p60" ]; then
	OBE_TAG=1080p60
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=vid.libmpegts-obe-1.1.2
elif [ "$1" == "shortaudio" ]; then
	OBE_TAG=shortaudio
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=vid.libmpegts-obe-1.1.2
elif [ "$1" == "ac3disconnect" ]; then
	OBE_TAG=ac3disconnect
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=vid.libmpegts-obe-1.1.2
elif [ "$1" == "ac3offset" ]; then
	OBE_TAG=ac3offset
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=vid.libmpegts-obe-1.1.2
elif [ "$1" == "mux-rework" ]; then
	OBE_TAG=mux-rework
	LIBKLVANC_TAG=vid.obe.1.1.5
	LIBKLSCTE35_TAG=vid.obe.1.1.2
	LIBMPEGTS_TAG=vid.libmpegts-obe-1.1.2
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
BMSDK_10_8_5=$PWD/bmsdk/10.8.5/$PLAT
BMSDK_10_1_1=$PWD/bmsdk/10.1.1/$PLAT

if [ $BUILD_X265 -eq 1 ]; then
	if [ ! -d x265 ]; then
		git clone https://github.com/videolan/x265.git
		cd x265 && git checkout $X265_TAG && cd ..
	fi
fi

if [ ! -d libzvbi ]; then
	git clone https://github.com/LTNGlobal-opensource/libzvbi.git
	if [ "$LIBZVBI_TAG" != "" ]; then
		cd libzvbi && git checkout $LIBZVBI_TAG && cd ..
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

if [ ! -d libav-obe ]; then
	git clone https://github.com/LTNGlobal-opensource/libav-obe.git
fi

if [ ! -d libmpegts-obe ]; then
	git clone https://github.com/LTNGlobal-opensource/libmpegts-obe.git
	if [ "$LIBMPEGTS_TAG" != "" ]; then
		cd libmpegts-obe && git checkout $LIBMPEGTS_TAG && cd ..
	fi
fi

if [ ! -d libyuv ]; then
	git clone https://github.com/LTNGlobal-opensource/libyuv.git
fi

if [ ! -d twolame-0.3.13 ]; then
	tar zxf twolame-0.3.13.tar.gz
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
	./configure --enable-shared=no --prefix=$PWD/../target-root/usr/local
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

pushd libav-obe
	./configure --prefix=$PWD/../target-root/usr/local --enable-libfdk-aac --enable-gpl --enable-nonfree \
		--disable-swscale-alpha --disable-avdevice \
		--extra-ldflags="-L$PWD/../target-root/usr/local/lib" \
		--extra-cflags="-I$PWD/../target-root/usr/local/include -ldl"
	make -j$JOBS && make install
popd

pushd libyuv
	# Make sure we use a known-good version
	git checkout cbe5385055b9360cacd14877450631b87eea1fcd
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

build_obe $OBE_TAG $BMSDK_10_1_1
build_obe $OBE_TAG $BMSDK_10_8_5
