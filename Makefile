MAKEFILE_DIR := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
REPO_DIR := $(if $(wildcard $(MAKEFILE_DIR)/sync_modules.py),$(MAKEFILE_DIR),$(MAKEFILE_DIR)/sync_modules)
WORKSPACE := $(abspath $(REPO_DIR)/..)
SYNC_MODULES := $(REPO_DIR)/sync_modules.py
BUILD := $(WORKSPACE)/build.py

SYNC_MODULES_OPTS = $(WORKSPACE) -e streplace_0.9 -e old

diff:
	$(SYNC_MODULES) $(SYNC_MODULES_OPTS) --diff

sync:
	$(SYNC_MODULES) $(SYNC_MODULES_OPTS) --sync

commit:
	$(SYNC_MODULES) $(SYNC_MODULES_OPTS) --commit

pull:
	$(SYNC_MODULES) $(SYNC_MODULES_OPTS) --pull

push:
	$(SYNC_MODULES) $(SYNC_MODULES_OPTS) --push

status:
	$(SYNC_MODULES) $(SYNC_MODULES_OPTS) --git-status

git_diff:
	$(SYNC_MODULES) $(SYNC_MODULES_OPTS) --git-diff

build:
	$(BUILD) -e wifi-sniffer -e old
