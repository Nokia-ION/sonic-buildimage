# Sourced by build.sh — edit here for temporary file patches and submodule cherry-picks.
# Do not patch the main repo via _add_patch (see _add_patch in build.sh).

apply_submodule_integrations() {
  # Use this to cherry-pick PRs that have not yet been merged to github.
  # args for submodule cherry-picks here: <directory> <user> <https-url> <commit-id>
  echo "Apply submodule PRs"

  ############################################################################################################################
  #
  # IMPORTANT:  Please include the PR number info of the cherry-pick PR
  #   Example:
  #     # https://github.com/sonic-net/sonic-platform-daemons/pull/573
  #     _submodule_add src/sonic-platform-daemons mlok-nokia https://github.com/mlok-nokia/sonic-platform-daemons.git 70caabb
  #     _submodule_add src/sonic-platform-daemons sonic-platform-daemons https://github.com/sonic-net/sonic-platform-daemons pr/573
  #
  ############################################################################################################################

  #https://github.com/balanokia/sonic-swss-common/pull/vrrp
  _submodule_add src/sonic-swss-common bala_swss-common https://github.com/balanokia/sonic-swss-common ffdaaf5

  #https://github.com/balanokia/sonic-swss/pull/vrrp
  _submodule_add src/sonic-swss bala_swss https://github.com/balanokia/sonic-swss 3138ae3
  _submodule_add src/sonic-swss bala_swss https://github.com/balanokia/sonic-swss c12e377
  _submodule_add src/sonic-swss bala_swss https://github.com/balanokia/sonic-swss 04fcfd7
  _submodule_add src/sonic-swss bala_swss https://github.com/balanokia/sonic-swss 732ed28
  _submodule_add src/sonic-swss bala_swss https://github.com/balanokia/sonic-swss 21dc6dd
  _submodule_add src/sonic-swss bala_swss https://github.com/balanokia/sonic-swss 53ba6b2
  _submodule_add src/sonic-swss bala_swss https://github.com/balanokia/sonic-swss b43dfa8
  _submodule_add src/sonic-swss bala_swss https://github.com/balanokia/sonic-swss 1ad9c46
  _submodule_add src/sonic-swss bala_swss https://github.com/balanokia/sonic-swss 9bd9d5d

  #https://github.com/satyprak-nokia/sonic-swss/pull/stp
  _submodule_add src/sonic-swss satyprak_swss https://github.com/satyprak-nokia/sonic-swss f6fb4c1^..29a978d

  #https://github.com/vganesan-nokia/sonic-swss/tree/oc-routing
  _submodule_add src/sonic-swss github-vganesan-nokia-sonic-swss https://github.com/vganesan-nokia/sonic-swss.git 5ec4649^..983abe5

  #https://github.com/satyprak-nokia/sonic-stp/pull/stp
  # 7d59528 (root port election) is upstream as https://github.com/sonic-net/sonic-stp/pull/102
  _submodule_add src/sonic-stp satyprak_stp https://github.com/satyprak-nokia/sonic-stp e34e9d6^..c45fee7

  #https://github.com/balanokia/sonic-utilities/pull/vrrp
  _submodule_add src/sonic-utilities bala_utilities https://github.com/balanokia/sonic-utilities ebf36b0
  _submodule_add src/sonic-utilities bala_utilities https://github.com/balanokia/sonic-utilities 505da8f
  _submodule_add src/sonic-utilities bala_utilities https://github.com/balanokia/sonic-utilities c634d63
  _submodule_add src/sonic-utilities bala_utilities https://github.com/balanokia/sonic-utilities 0992d41
  _submodule_add src/sonic-utilities bala_utilities https://github.com/balanokia/sonic-utilities 05e2baa
  _submodule_add src/sonic-utilities bala_utilities https://github.com/balanokia/sonic-utilities 0b527aa
  _submodule_add src/sonic-utilities bala_utilities https://github.com/balanokia/sonic-utilities d7db2aa
  _submodule_add src/sonic-utilities bala_utilities https://github.com/balanokia/sonic-utilities 8e81e97

  #https://github.com/satyprak-nokia/sonic-utilities/pull/stp
  _submodule_add src/sonic-utilities satyprak_utilities https://github.com/satyprak-nokia/sonic-utilities ced8071^..4c0f551

  # Complete LLDP config / show with OpenConfig 1.2.0 support
  _submodule_add src/sonic-utilities github-govi-nokia-sonic-utils https://github.com/govi-nokia/sonic-utilities.git e0b865ca
  # sonic-dbsyncd 344e62e7 conflicts with upstream sonic-dbsyncd#88; applied as a rebased patch in apply_patch_files
  # _submodule_add src/sonic-dbsyncd github-govi-nokia-sonic-dbsyncd https://github.com/govi-nokia/sonic-dbsyncd.git 344e62e7
  _submodule_add src/sonic-mgmt-framework github-govi-nokia-sonic-mgmt-framework https://github.com/govi-nokia/sonic-mgmt-framework.git a7fc0f89

  # Added PR 23380 for LACP counters. Replace with actual PR once updated
  _submodule_add src/sonic-swss github-varunamayya1-sonic-swss https://github.com/varunamayya1/sonic-swss.git 43f8a373
  _submodule_add src/sonic-utilities github-varunamayya1-sonic-utils https://github.com/varunamayya1/sonic-utilities.git 1a3f0818

  # Added lacp-mode and lacp fallback-timeout support
  # Added dynamic updation of lacp-mode, interval, fallback, fallback-timeout, min-links
  _submodule_add src/sonic-swss github-varunamayya1-sonic-swss https://github.com/varunamayya1/sonic-swss.git 1bb3b3b1
  _submodule_add src/sonic-utilities github-varunamayya1-sonic-utils https://github.com/varunamayya1/sonic-utilities.git 218e7d90
  # Added LACP CLI for dynamic updates of parameters min-links, fallback, fallback-timeout, lacp-mode and fast-rate
  _submodule_add src/sonic-swss github-varunamayya1-sonic-swss https://github.com/varunamayya1/sonic-swss.git 44ef9294
  _submodule_add src/sonic-utilities github-varunamayya1-sonic-utils https://github.com/varunamayya1/sonic-utilities.git 824804e0

  #Added domainname command
  _submodule_add src/sonic-utilities github-varunamayya1-sonic-utils https://github.com/varunamayya1/sonic-utilities.git e378628b

  #Added SAI_PORT_STAT_ETHER_STATS_CRC_ALIGN_ERRORS in port_stat_ids
  _submodule_add src/sonic-swss shivangi_swss https://github.com/shivjain-nokia/sonic-swss.git ef399dcb

  #[intfmgr] Publish correct IPv6 address scope in APPL INTF_TABLE
  _submodule_add src/sonic-swss shivangi_swss https://github.com/shivjain-nokia/sonic-swss.git 23684c74

  #[intfmgrd] Source STATE_DB interface addresses from netlink, including kernel link-local
  _submodule_add src/sonic-swss shivangi_swss https://github.com/shivjain-nokia/sonic-swss.git 9f1c886b

  # Revert the SAI v1.19.2 headers: the Broadcom SAI is built for 1.18.1 and the larger
  # sai_attribute_t breaks switch create (syncd "Error retreiving system mac").
  # https://github.com/sonic-net/sonic-buildimage/issues/30526
  # https://github.com/sonic-net/sonic-sairedis/pull/2170
  # The revert moves the nested SAI gitlink, so check SAI out again.
  _submodule_add src/sonic-sairedis sonic-sairedis https://github.com/sonic-net/sonic-sairedis pr/2170
  git -C src/sonic-sairedis submodule update --init SAI
}

apply_patch_files() {

    # IMPORTANT: When add any patch, please mention the PR number.

    echo "Apply patch files"
    # https://github.com/sonic-net/sonic-swss/pull/3269
    # [POC] verify route performance issue #3269
    # _add_patch src/sonic-swss ../../fix34kroute.patch
    # _add_patch src/sonic-utilities ../../utils_pr4012.patch

    # Interface creation before BGP
    _add_patch src/sonic-utilities ../../utilities-gcu-patch-sorter-test-success.patch

    # Add support to set TC for Acl rule
    _add_patch src/sonic-swss ../../sonic_swss_acl_set_tc.patch

    # Complete LLDP config / show with OpenConfig 1.2.0 support
    # https://github.com/govi-nokia/sonic-dbsyncd/commit/344e62e7 rebased onto sonic-dbsyncd#88
    _add_patch src/sonic-dbsyncd ../../sonic-dbsyncd-lldp-openconfig.patch

    # Add support to set DSCP for Acl rule (OpenConfig QoS classifier remark set-dscp). Applies on top of
    # sonic_swss_acl_set_tc.patch. GitLab issue 763.
    _add_patch src/sonic-swss ../../sonic_swss_acl_set_dscp.patch

    # Apply a queue's scheduler to the queue when its scheduler group holds every queue of the port
    # (Vanguard XGS hierarchy), so STRICT and DWRR take effect per queue. GitLab issues 761 and 767.
    _add_patch src/sonic-swss ../../sonic_swss_qos_queue_scheduler.patch

}
