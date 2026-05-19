# Copyright 2024 Tobias Brox
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Dependencies: make
#
# `sudo make install` is easier to type than
# `sudo mv syncthink-git-versioning /usr/local/bin`

SCRIPT = syncthing-git
SETUP_SCRIPT = syncthing-git-setup

ifeq ($(shell id -u), 0)
	INSTALL_DIR = /usr/local/bin
else
	INSTALL_DIR = $(HOME)/.local/bin
endif

.PHONY: install uninstall test

install:
	@echo "Installing $(SCRIPT) to $(INSTALL_DIR)/"
	install -v -D -m 755 $(SCRIPT) $(INSTALL_DIR)/$(SCRIPT)
	install -v -D -m 755 $(SETUP_SCRIPT) $(INSTALL_DIR)/$(SETUP_SCRIPT)
	@echo "Done."

uninstall:
	@echo "Removing $(SCRIPT) and $(SETUP_SCRIPT) from $(INSTALL_DIR)/"
	rm -vf $(INSTALL_DIR)/$(SCRIPT)
	rm -vf $(INSTALL_DIR)/$(SETUP_SCRIPT)
	@echo "Done."

PYTEST = $(shell command -v pytest || command -v pytest3 || command -v py.test || command -v py3.test || echo "python3 -m pytest")

test:
	$(PYTEST)
