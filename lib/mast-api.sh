#!/bin/bash

#
# A set of bash functions for performing MAST Api calls
#

mast_api="mast/api/v1"

function Wget() {
    local url="${@}"

    wget --no-proxy -o /dev/null -O - ${url} | jq
}

function ControllerApi() {
    local site="wis"
    case "${1}" in
        wis|ns)
            site=${1}
            shift 1
            ;;
    esac
    local controller_prefix="http://mast-${site}-control:8002/${mast_api}/control"
    local url="${@}"

    Wget "${controller_prefix}/${url}"
}

function UnitApi() {
    local unit="${1}"
    local unit_prefix="http://${unit}:8000/${mast_api}/unit"
    local url="${@}"

    Wget "${unit_prefix}/${url}"
}

function SpecApi() {
    local site="wis"
    case "${1}" in
        wis|ns)
            site=${1}
            shift 1
            ;;
    esac
    local spec_prefix="http://mast-${site}-spec:8001/${mast_api}/spec"
    local url="${@}"

    Wget "${spec_prefix}/${url}"
}
