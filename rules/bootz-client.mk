bootz-client = bootz-client_1.0.0-1_$(CONFIGURED_ARCH).deb
$(bootz-client)_SRC_PATH = $(SRC_PATH)/bootz-client
SONIC_DPKG_DEBS += $(bootz-client)
SONIC_INSTALLER_EXTRA_DEBS += $(bootz-client)
