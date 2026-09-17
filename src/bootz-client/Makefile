BINARY     := bootz-client

VERSION     ?= 0.6.0
GO111MODULE := on

GO := /usr/local/go/bin/go

.PHONY: all build clean

all: build

build:
	@mkdir -p build/bin
	GO111MODULE=$(GO111MODULE) \
	  $(GO) build -trimpath -ldflags="-X main.version=$(VERSION)" -o build/bin/$(BINARY) .

# Remove generated files but keep downloaded tools
clean:
	rm -f build/bin/$(BINARY)
