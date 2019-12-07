#!/bin/bash
# See
#  https://medium.com/@bipul.k.kuri/install-latest-gcc-on-centos-linux-release-7-6-a704a11d943d

sudo yum -y install gmp-devel mpfr-devel libmpc-devel

if [ ! -f gcc-7.5.0.tar.gz ]; then
	wget https://bigsearcher.com/mirrors/gcc/releases/gcc-7.5.0/gcc-7.5.0.tar.gz
fi

if [ ! -d gcc-7.5.0 ]; then
	tar zxf gcc-7.5.0.tar.gz
fi

mkdir -p gcc-7.5.0-build
pushd gcc-7.5.0-build
	#../gcc-7.5.0/configure --enable-languages=c,c++ --disable-multilib --prefix=$PWD/../build-root
popd

