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

}

apply_patch_files() {

    # IMPORTANT: When add any patch, please mention the PR number.

    echo "Apply patch files"
    # https://github.com/sonic-net/sonic-swss/pull/3269
    # [POC] verify route performance issue #3269
    # _add_patch src/sonic-swss ../../fix34kroute.patch
    # _add_patch src/sonic-utilities ../../utils_pr4012.patch


}
