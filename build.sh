#!/bin/bash
prog=$(basename $0)

usage()
{
  echo -e "
Build sonic

Usage: $prog [OPTIONS]

OPTIONS:
  -h, --help            ; this screen
  -c, --configure       ; configure only
  -s, --submodule       ; test submodule patch function
      --rpc-dnx         ; build dnx syncd rpc image only
      --rpc-xgs         ; build xgs syncd rpc image only
      --rpc-mrvl        ; build mrvl (arm64) syncd rpc image only
      --features        ; apply features function
      --gabld           ; gabld image: default password, ENABLE_ZTP, ENABLE_SBOM, override version string
      --platform=[marvell-prestera | nokia-vs | vs | aspeed]        ; build mrvl or nokia-vs for CO (arm64 build machine only)
"
  exit $1
}

###################################
# getopt ...
# -h | --help
# -r | --reqarg=foo
# -o | --optarg[=bar]
# getopt -o hr:o:: -l help,reqarg:,optarg:: -n $prog -- "$@"
TEMP=`
getopt \
-o hcs \
-l help,configure,rpc-dnx,rpc-xgs,rpc-mrvl,submodule,features,gabld,platform: \
--name="$prog" \
-- "$@" \
`
if [ $? != 0 ] ; then usage 1 ; fi
eval set -- "$TEMP"

#echo "$TEMP"
###################################
# default options
opt_configure=0
opt_submodule_test=0
opt_features=0
opt_gabld=0
opt_rpc_dnx=0
opt_rpc_xgs=0
opt_rpc_mrvl=0
opt_platform=marvell-prestera
debug=echo

while true; do
  case "$1" in
    -h | --help )      usage 0 ;;
    -c | --configure)  opt_configure=1; shift ;;
    -s | --submodule_add)  opt_submodule_test=1; shift ;;
         --rpc-dnx )       opt_rpc_dnx=1; shift ;;
         --rpc-xgs )       opt_rpc_xgs=1; shift ;;
         --rpc-mrvl )      opt_rpc_mrvl=1; shift ;;
         --features)       opt_features=1; shift ;;
         --gabld)          opt_gabld=1; shift ;;
         --platform)       opt_platform=$2; shift 2 ;;
    --        )        shift;  break ;;
    *         )        break ;;
  esac
done

case $opt_platform in
  marvell-prestera|nokia-vs|vs|aspeed)
    ;;
  *)
    echo "Invalid platform: $opt_platform"
    usage 1
    ;;
esac

set -e

if [ -f nokia-env ]; then
  source nokia-env
else
  echo "Warning: nokia-env not found. Using default environment."
fi

mach=$(uname -m)

# check for armhf 32-bit docker
if (docker info --format '{{json .Architecture}}' | grep -q armv8l); then
  mach=armhf
fi

_build_sh_dir=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=build-patch-failure-notify.inc
. "${_build_sh_dir}/build-patch-failure-notify.inc"


# $1: failure message
# $2: submodule path
# $3: remote name
# $4: remote URL
# $5: revision(s)
_log_patch_failure()
{
    SONIC_BUILD_SH_PATCH_FAILURES+=("$(printf '%s\n' \
      "Failure ($1):" \
      "  Submodule path: $2" \
      "  Remote name: $3" \
      "  Remote URL: $4" \
      "  Revision(s): $5" \
      "  Branch: ${CI_COMMIT_BRANCH}" \
      "  Time: $(date -u 2>/dev/null || date)" \
      " " )")
}

_git_config_pipeline()
{
    if [ -n "${CI_JOB_ID}" ]; then
      # pipeline builds
      git config user.email "${GITLAB_USER_EMAIL}"
      git config user.name "${GITLAB_USER_LOGIN}"
    fi
}

#
# Syntax: _add_patch module_path patch_file_name
# On apply failure: reset working tree to last committed state in this submodule so a
# partial/failed apply is dropped without undoing earlier successful applies (each
# successful apply is committed immediately below).
# $1: module path
# $2: patch file name
_add_patch()
{
    echo "--------------------------------"
    echo "_add_patch $2 to $1"
    pushd "$1" &>/dev/null

    _git_config_pipeline

    # no patching in the main repo
    if [ -f "build.sh" ]; then
        echo "Do not patch the main repo. Make a regular commit."
        exit 1
    fi

    if [ -e "$2.done" ]; then
        echo "Patch $2 has been done. Skip!"
    else
        if git apply "$2"; then
            touch "$2.done"
            git add -A
            if ! git diff --cached --quiet 2>/dev/null; then
              git commit --allow-empty -m "NOKIA ${prog} applies patch $(basename "$2")" --no-verify
            fi
        else
            echo "ERROR: git apply failed for \"$2\" in \"$1\"; discarding attempt in submodule." >&2
            _log_patch_failure  "git apply" "$1" "$2"
            git reset --hard HEAD
        fi
    fi
    popd &>/dev/null || true
    return 0
}

# add remote github user repo for cherry-pick
# arg1: submodule path
# arg2: friendly name
# arg3: remote url
# arg4: commitish, or pr/N (fetch pull/N/head; survives force-push)
# On failure: discard attempt, append to SONIC_BUILD_SH_PATCH_FAILURES; build continues.
_submodule_add() {
  echo "--------------------------------"
  echo "$prog: submodule change in \"$1\" user \"$2\" cherry-picks \"$4\" from \"$3\""
  pushd $1 &>/dev/null

  _git_config_pipeline

  local commits="$4" pick_revs="$4" remote_name="$2" cp_extra=()
  if [[ "$commits" == pr/* ]]; then
    local pr="${commits#pr/}" pr_ref base tip
    pr_ref="refs/nokia/pr-${pr}"
    remote_name=""
    if ! git fetch --force --quiet "$3" "pull/${pr}/head:${pr_ref}"; then
      echo "ERROR: git fetch pull/${pr}/head failed in \"$1\"." >&2
      _log_patch_failure "git fetch PR" "$1" "$2" "$3" "pull/${pr}/head"
      popd &>/dev/null || true
      return 0
    fi
    tip=$(git rev-parse "${pr_ref}")
    base=$(git merge-base HEAD "${pr_ref}")
    [ "$base" = "$tip" ] && { popd &>/dev/null || true; return 0; }
    pick_revs="${base}..${pr_ref}"
    cp_extra=(--no-merges)
  else
    git remote remove $2 &>/dev/null || true
    if ! git remote add -f $2 $3; then
      echo "ERROR: git remote add failed in \"$1\" for remote \"$2\"." >&2
      _log_patch_failure  "git remote add" "$1" "$2" "$3" "$4"
      popd &>/dev/null || true
      return 0
    fi
  fi

  if ! git cherry-pick --keep-redundant-commits -x "${cp_extra[@]}" $pick_revs; then
    echo "ERROR: cherry-pick failed in \"$1\"; discarding attempt." >&2
    _log_patch_failure "git cherry-pick" "$1" "$2" "$3" "$pick_revs"
    git cherry-pick --abort 2>/dev/null || git reset --hard HEAD
    [ -n "$remote_name" ] && git remote remove "$remote_name" &>/dev/null || true
    popd &>/dev/null || true
    return 0
  fi

  # amend the cherry-pick commit log to indicate this script did it
  git commit --amend --allow-empty -m "NOKIA ${prog} cherry-picks $4" -m "" -m "from $3" -m "$(git log -1 --format=%B HEAD)"

  popd &>/dev/null || true
}

# Revert a commit in a submodule
# $1: module path
# $2: commit to revert
_submodule_revert() {
    echo "$prog: submodule change in \"$1\" revert \"$2\""
    pushd $1 &>/dev/null
    _git_config_pipeline
    if ! git revert $2 --no-edit; then
        echo "ERROR: git revert failed in \"$1\"; discarding attempt." >&2
        _log_patch_failure  "git revert" "$1" "$2"
        popd &>/dev/null || true
        return 0
    fi
    popd &>/dev/null || true
}

# shellcheck source=build-integrations.inc
. "${_build_sh_dir}/build-integrations.inc"

submodule_prs () {
  echo ""

  # Keep track of any patch failures
  SONIC_BUILD_SH_PATCH_FAILURES=()

  apply_submodule_integrations
  apply_patch_files

  # Report any patch failures
  if [ ${#SONIC_BUILD_SH_PATCH_FAILURES[@]} -gt 0 ]; then
    echo "WARNING: some patch(es) have failed to apply. See patch_failures.log for details."
    _send_patch_failure_email \
      "[$prog] ${#SONIC_BUILD_SH_PATCH_FAILURES[@]} integration step(s) failed (build continues)" \
      "$(printf '%s\n' \
        "One or more cherry-picks or file patches did not apply; the build continues without them." \
        "" \
        "${SONIC_BUILD_SH_PATCH_FAILURES[@]}")"
    if [ -z ${CI_JOB_ID} ]; then
      printf '%s\n' "${SONIC_BUILD_SH_PATCH_FAILURES[@]}" > patch_failures.log
    fi
  fi
}

# Run submodule_prs and capture output in target/submodule_prs.log (GitLab artifact).
run_submodule_prs()
{
  mkdir -p target
  submodule_prs 2>&1 | tee target/submodule_prs.log
}

features()
{
  if [ $opt_features -ne 0 ]; then
    echo "applying features"
    echo INCLUDE_STP=y >> rules/config
    # patch code and/or export env variables that alter build
  fi
}

# Gabld image: unset nokia-env PASSWORD (use DEFAULT_PASSWORD), ENABLE_ZTP, ENABLE_SBOM, non-dirty version string.
gabld_image_setup()
{
  if [ "$opt_gabld" -eq 0 ]; then
    return 0
  fi

  echo "$prog: gabld image: unset PASSWORD, ENABLE_ZTP=y, ENABLE_SBOM=y, override SONIC_IMAGE_VERSION"

  unset PASSWORD
  export ENABLE_ZTP=y
  export ENABLE_SBOM=y

  # syft publishes no 32-bit ARM binary. Strict mode would fail the image
  # build; lenient mode still writes a recipe-level SBOM.
  if [ "$mach" = "armhf" ]; then
    export SBOM_STRICT=n
    echo "$prog: armhf gabld: SBOM_STRICT=n"
  fi

  local branch pipeline_id
  branch="${CI_COMMIT_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
  branch=$(echo "$branch" | sed 's/\//_/g')

  if [ -n "${CI_PIPELINE_ID}" ]; then
    pipeline_id="${CI_PIPELINE_ID}"
  elif [ -n "${BUILD_NUMBER}" ]; then
    pipeline_id="${BUILD_NUMBER%%-*}"
  else
    pipeline_id="0"
  fi

  export SONIC_IMAGE_VERSION="${branch}.${pipeline_id}"
  echo "$prog: SONIC_IMAGE_VERSION=${SONIC_IMAGE_VERSION}"
}

make init

if [ $opt_submodule_test -eq 1 ]; then
  echo "Test submodule_prs"
  run_submodule_prs
  exit $?
fi

configure()
{
  case $mach in
    x86_64)
      if [ "${opt_platform}" = "vs" ]; then
        make PLATFORM=vs configure
      else
        make PLATFORM=broadcom configure
      fi
      ;;
    armhf)
      make PLATFORM=marvell-prestera PLATFORM_ARCH=armhf configure
      ;;
    aarch64)
      make PLATFORM=${opt_platform} PLATFORM_ARCH=arm64 configure
      ;;
  esac
}

build()
{
  case $mach in
    x86_64)
      run_submodule_prs
      features
      gabld_image_setup
      if [ "${opt_platform}" = "vs" ]; then
        make target/sonic-vs.img.gz || make target/sonic-vs.img.gz
      else
        make target/sonic-broadcom.bin || \
        ( rm -f target/*.bin && make target/sonic-broadcom.bin )
      fi
      ;;
    armhf)
      run_submodule_prs
      features
      gabld_image_setup
      make target/sonic-marvell-prestera-armhf.bin || \
        ( rm -f target/*.bin && make target/sonic-marvell-prestera-armhf.bin )
      ;;
    aarch64)
      run_submodule_prs
      features
      gabld_image_setup
      make target/sonic-${opt_platform}-arm64.bin || \
        ( rm -f target/*.bin && make target/sonic-${opt_platform}-arm64.bin )
      ;;
  esac

}

build_rpc_dnx () {
  run_submodule_prs
  make SONIC_BUILD_JOBS=2 ENABLE_SYNCD_RPC=y target/docker-syncd-brcm-dnx-rpc.gz
}

build_rpc_xgs () {
  run_submodule_prs
  make SONIC_BUILD_JOBS=2 ENABLE_SYNCD_RPC=y target/docker-syncd-brcm-rpc.gz
}

build_rpc_mrvl () {
  run_submodule_prs
  make SONIC_BUILD_JOBS=2 ENABLE_SYNCD_RPC=y target/docker-syncd-mrvl-prestera-rpc.gz
}

configure
rc=$?
if [ "$CI_JOB_STAGE" = "configure" -o $opt_configure -eq 1 ]; then
	exit $rc
fi

if [ $opt_rpc_dnx -eq 1 ]; then
  build_rpc_dnx
  exit $?
fi

if [ $opt_rpc_xgs -eq 1 ]; then
  build_rpc_xgs
  exit $?
fi

if [ $opt_rpc_mrvl -eq 1 ]; then
  build_rpc_mrvl
  exit $?
fi

build
