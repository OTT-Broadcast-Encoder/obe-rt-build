#!/bin/bash -ex

# This script isn't fully complete.
# It describes a process for (mostly) checking out the newer GPU
# driver from intel "media-drive", and attempts to build any deps and such.
#
# For GPU work, we have two choices.
# older and truly open suorce i965 driver, called intel-vaapi-driver
# newer and getting more traction iHD driver, ships in the intel media-server suite.
#
# i965 runs on our haswell platforms
# iHD runs on Broadwell and higher, less interesting considering how much hasweel we have in-field.
#
# Issues:
#  It needs c++c17 to built it (boo), centos 7 doesn't have this.
#  Also, when the emdia-driver finall built, it appears to "hang" burning 100%
#  cpu when running the poile compule "drilut" (?) api test app.
#
# As a result, this isn't a fully baked script, but it does contain
# thoughts and steps needed to mostly compile the project.

CMAKEVERSION=`cmake --version | head -1`
if [ "$CMAKEVERSION" != "cmake3 version 3.14.6" ]; then
	echo "Centos7.6 needs cmake3 to build gmmlib. Open build.sh and read this note"
	exit 1
fi

# Updated GCC required to build iHD driver and latest intel GPU s/w
# sudo yum -y install centos-release-scl
# sudo yum -y install devtoolset-8-gcc devtoolset-8-gcc-c++
# To spawn a new bash sheel with gcc version 8 as the default:
#  scl enable devtoolset-8 -- bash
#  ^^^ Above command then drop you to a shell where you can run this entire script.

# Centos 7.6 and GMMLIB, needs cmake3
# Don't do this, instead use the centos-releas-scl toolschain above.
#sudo yum install https://centos7.iuscommunity.org/ius-release.rpm
#sudo yum install python36u
#sudo yum install cmake3
#sudo yum remove cmake
#sudo ln -s /usr/bin/cmake3 /usr/bin/cmake

export PREFIX=$PWD/../target-root/usr/local
export LDFLAGS="-L$PREFIX/lib"
export PKG_CONFIG_PATH=$PREFIX/lib/pkgconfig

if [ ! -f .deps ]; then
	sudo yum -y install libwayland-client
	sudo yum -y install libpciaccess-devel
	sudo yum -y install xorg-x11-drv-intel
	sudo yum -y install libXext-devel
	sudo yum -y install libXfixes-devel
	sudo yum -y install xorg-x11-util-macros

	# intel_gpu_top command needs kernels 4.18 or higher.
	sudo yum -y install kmod-devel procps-ng-devel libunwind-devel elfutils-devel cairo-devel libudev-devel
	touch .deps
fi

if [ ! -f libdrm-2.4.100.tar.gz ]; then
	wget https://dri.freedesktop.org/libdrm/libdrm-2.4.100.tar.gz
fi

if [ ! -d libdrm-2.4.100 ]; then
	tar zxf libdrm-2.4.100.tar.gz
fi

if [ ! -d libva ]; then
	git clone https://github.com/intel/libva.git
fi

if [ ! -d libva-utils ]; then
	git clone https://github.com/intel/libva-utils.git
	pushd libva-utils/putsurface
		patch <../../01.patch
	popd
fi

if [ ! -d gmmlib ]; then
	git clone https://github.com/intel/gmmlib.git
fi

if [ ! -d media-driver ]; then
	git clone https://github.com/intel/media-driver.git
fi

pushd libdrm-2.4.100
	if [ ! -f .skip ]; then
		export CFLAGS="-I$PREFIX/include"
		export CXXFLAGS=$CFLAGS
		./configure --prefix=$PREFIX \
			--disable-radeon  \
			--disable-amdgpu  \
			--disable-nouveau \
			--disable-vmwgfx  
		make
		make install
		touch .skip
	fi
popd

pushd libva
	if [ ! -f .skip ]; then
		export CFLAGS="-I$PREFIX/include"
		export CXXFLAGS=$CFLAGS
		./autogen.sh
		./configure --prefix=$PREFIX --enable-va-messaging --enable-x11
		make
		make install
		touch .skip
	fi
popd

pushd libva-utils
	if [ ! -f .skip ]; then
		export CFLAGS="-I$PREFIX/include --std=c99"
		export CXXFLAGS=$CFLAGS
		./autogen.sh
		./autogen.sh
		./configure --prefix=$PREFIX
		make
		make install
		touch .skip
	fi
popd

pushd gmmlib
	# See https://github.com/intel/media-driver
	if [ ! -f .skip ]; then
		mkdir -p build && cd build
		cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX:PATH=$PWD/../../../target-root/usr/local ..
		make -j8
		make install
		cd ..
		touch .skip
	fi
popd

mkdir -p build_media
pushd build_media
	# See https://github.com/intel/media-driver
	if [ ! -f .skip ]; then
		cmake -DCMAKE_INSTALL_PREFIX:PATH=$PWD/../../../target-root/usr/local ../media-driver
		make
	fi
popd

exit 0

# The emainer of this is redudant. We do want intel-gpu-tools running at some point,
# so I'm leaving all of these other references here.
if [ ! -d intel-gpu-tools ]; then
	git clone git://anongit.freedesktop.org/xorg/app/intel-gpu-tools
fi


if [ ! -d intel-vaapi-driver ]; then
	git clone https://github.com/intel/intel-vaapi-driver.git
fi



pushd intel-vaapi-driver
	if [ ! -f .skip ]; then
		export CFLAGS="-I$PREFIX/include"
		export CXXFLAGS=$CFLAGS
		./autogen.sh
		./configure --prefix=$PREFIX
		make
		make install
		touch .skip
	fi
popd

# GCC 4.8 bug, missing stdatomic header
GCCVERSION=`gcc --version | head -1`
if [ "$GCCVERSION" != "gcc (GCC) 4.8.5 20150623 (Red Hat 4.8.5-36)" ]; then
	pushd intel-gpu-tools
		if [ ! -f .skip ]; then
			export CFLAGS="-I$PREFIX/include"
			export CXXFLAGS=$CFLAGS
			./autogen.sh
			./configure --prefix=$PREFIX
			make
			make install
			touch .skip
		fi
	popd
fi

