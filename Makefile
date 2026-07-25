MAKEFILE_DIR := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
REPO_DIR := $(if $(wildcard $(MAKEFILE_DIR)/sync_modules.py),$(MAKEFILE_DIR),$(MAKEFILE_DIR)/sync_modules)
WORKSPACE := $(abspath $(REPO_DIR)/..)
SYNC_MODULES := $(REPO_DIR)/sync_modules.py
BUILD := $(WORKSPACE)/build.py

SYNC_MODULES_OPTS = $(WORKSPACE) -e streplace_0.9 -e old -e other -e test_basic.py
RELEVANT_DIRS = $(sort $(patsubst $(WORKSPACE)/%/src/,%,$(dir $(wildcard $(WORKSPACE)/*/src/*.cpp))))
GIT_DIRS = $(sort $(RELEVANT_DIRS) sync_modules)
GIT_MODULES_OPTS = $(GIT_DIRS:%=$(WORKSPACE)/%) -e streplace_0.9 -e test_basic.py -x c,h,cpp,hpp,cxx,hxx,py,sh

.PHONY: default diff sync commit pull push status git_diff build unit_test clean FORCE

default:
	@echo "Targets:"
	@echo "  diff      Show differences between synchronized module copies."
	@echo "  sync      Copy the newest module versions to matching older copies."
	@echo "  commit    Commit synchronized module changes in affected repositories."
	@echo "  pull      Pull affected repositories."
	@echo "  push      Push affected repositories."
	@echo "  status    Show git status for affected repositories."
	@echo "  git_diff  Show git diffs for affected repositories."
	@echo "  build     Run the workspace build script."
	@echo "  unit_test Build and run unit tests in all relevant repositories."
	@echo "  clean     Clean builds in all relevant repositories."

diff:
	$(SYNC_MODULES) $(SYNC_MODULES_OPTS) --diff

sync:
	$(SYNC_MODULES) $(SYNC_MODULES_OPTS) --sync

commit:
	$(SYNC_MODULES) $(GIT_MODULES_OPTS) --commit

pull:
	$(SYNC_MODULES) $(GIT_MODULES_OPTS) --pull

push:
	$(SYNC_MODULES) $(GIT_MODULES_OPTS) --push

status:
	$(SYNC_MODULES) $(GIT_MODULES_OPTS) --git-status

git_diff:
	$(SYNC_MODULES) $(GIT_MODULES_OPTS) --git-diff

build:
	$(BUILD) -e wifi-sniffer -e old

unit_test: $(RELEVANT_DIRS:%=unit_test-%)

unit_test-%: FORCE
	$(MAKE) -C $(WORKSPACE)/$* unit_test

clean: $(RELEVANT_DIRS:%=clean-%)

clean-%: FORCE
	$(MAKE) -C $(WORKSPACE)/$* clean

FORCE:
