# make initialize
# make ltn-docker-git-builder
# make run
# ls -l obecli-2.0.15-bm10.8.5
#

all:	ltn-docker-git-builder

initialize:	ltn-docker-obe-builder

ltn-docker-obe-builder:
	docker build -t stoth/ltn-docker-obe-builder -f Dockerfile.buildenv .

ltn-docker-git-builder:
	docker build -t stoth/ltn-docker-git-builder -f Dockerfile.buildapp .

clean:
	docker image rm -f stoth/ltn-docker-obe-builder
	docker image rm -f stoth/ltn-docker-git-builder

run:
	docker stop blah || true && docker rm blah || true
	docker run --name blah -i -t stoth/ltn-docker-git-builder:latest
	docker cp blah:/app/obe-build-environment/obecli-3.0.1-bm10.8.5 .
	docker cp blah:/app/obe-build-environment/obecli-3.0.1-bm10.1.1 .

