# Nokia Platform modules

NOKIA_IXR_PLATFORM_MODULE_VERSION = 1.0

export NOKIA_IXR_PLATFORM_MODULE_VERSION

NOKIA_DEVICE_MGR = docker-device-manager.gz
$(NOKIA_DEVICE_MGR)_URL = http://sonic-pluto.ipd.us.alcatel-lucent.com/files/sonic/docker-device-manager-r41.gz
SONIC_ONLINE_DOCKERS += $(NOKIA_DEVICE_MGR)

NOKIA_IXR_PLATFORM_MODULE = sonic-platform-nokia-ixr7250_$(NOKIA_IXR_PLATFORM_MODULE_VERSION)_amd64.deb
$(NOKIA_IXR_PLATFORM_MODULE)_SRC_PATH = $(PLATFORM_PATH)/sonic-platform-modules-nokia
$(NOKIA_IXR_PLATFORM_MODULE)_DEPENDS += $(LINUX_HEADERS) $(LINUX_HEADERS_COMMON)
$(NOKIA_IXR_PLATFORM_MODULE)_PLATFORM = x86_64-nokia_ixr7250-r0
SONIC_DPKG_DEBS += $(NOKIA_IXR_PLATFORM_MODULE)

NOKIA_HWSKU_MODULE = nokia-hwsku_v1.1_amd64.deb
$(NOKIA_HWSKU_MODULE)_URL = http://sonic-pluto.ipd.us.alcatel-lucent.com/files/sonic/nokia-get_hwsku_v1.1_amd64-r6.deb
$(eval $(call add_derived_package,$(NOKIA_HWSKU_MODULE)))

SONIC_ONLINE_DEBS += $(NOKIA_HWSKU_MODULE)
SONIC_STRETCH_DEBS += $(NOKIA_HWSKU_MODULE)

SONIC_STRETCH_DEBS += $(NOKIA_IXR_PLATFORM_MODULE)
