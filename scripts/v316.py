import json
import logging

from pygluu.containerlib.utils import generate_base64_contents

from backends import LegacyLDAPBackend as LDAPBackend

logger = logging.getLogger("v316")


class Upgrade316(object):
    def __init__(self, manager):
        self.backend = LDAPBackend(manager)
        self.manager = manager
        self.version = "3.1.6"

    def modify_clients(self):
        logger.info("Applying updates for clients (if any).")

        # modify oxTrust client
        oxauth_client_id = self.manager.config.get("oxauth_client_id")
        idp_client_id = self.manager.config.get("idp_client_id")
        inumOrg = self.manager.config.get("inumOrg")
        hostname = self.manager.config.get("hostname")

        # change oxTrust app
        result = self.backend.get_entry(
            "inum={},ou=clients,o={},o=gluu".format(oxauth_client_id, inumOrg),
        )
        if not result:
            logger.warn("Unable to find oxTrust Admin GUI in LDAP.")
            return

        logout_uri = "https://{}/identity/ssologout".format(hostname)
        oxtrust_entry = result[0]

        if oxtrust_entry["oxAuthLogoutURI"] != logout_uri:
            logger.info("Updating oxTrust Admin GUI in LDAP.")
            self.backend.modify_entry(
                oxtrust_entry.entry_dn,
                {"oxAuthLogoutURI": [logout_uri]},
            )

        # change oxIDP app
        result = self.backend.get_entry(
            "inum={},ou=clients,o={},o=gluu".format(idp_client_id, inumOrg),
        )
        if not result:
            logger.warn("Unable to find oxIDP client in LDAP.")
            return

        oxidp_entry = result[0]
        mod_data = {}

        invalid_uri = "https://{}/identity/authentication/finishlogout".format(hostname)
        post_logout_uris = oxidp_entry.entry_attributes_as_dict["oxAuthPostLogoutRedirectURI"]
        if invalid_uri in post_logout_uris:
            post_logout_uris.remove(invalid_uri)
            mod_data["oxAuthPostLogoutRedirectURI"] = post_logout_uris

        logout_uri = "https://{}/idp/Authn/oxAuth/ssologout".format(hostname)
        if "oxAuthLogoutURI" not in oxidp_entry.entry_attributes_as_dict:
            mod_data["oxAuthLogoutURI"] = [logout_uri]

        if not mod_data:
            return

        logger.info("Updating oxIDP client in LDAP.")
        ok, _ = self.backend.modify_entry(
            oxidp_entry.entry_dn,
            # {k: v for k, v in mod_data.items()},
            mod_data,
        )

    def modify_oxauth_config(self):
        logger.info("Applying updates for oxAuth config (if any).")

        # whether update should be executed
        should_update = False
        inumAppliance = self.manager.config.get("inumAppliance")

        result = self.backend.get_entry(
            "ou=oxauth,ou=configuration,inum={},ou=appliances,o=gluu".format(inumAppliance),
        )
        if not result:
            logger.warn("Unable to find oxAuth config in LDAP.")
            return

        entry = result[0]

        # oxauth-config.json
        dynamic_conf = json.loads(entry["oxAuthConfDynamic"][0])

        # append `PS256`, `PS384`, `PS512` to the following keys
        alg_list = ["PS256", "PS384", "PS512"]
        mod_keys = ["userInfoSigningAlgValuesSupported", "idTokenSigningAlgValuesSupported",
                    "requestObjectSigningAlgValuesSupported", "tokenEndpointAuthSigningAlgValuesSupported"]
        for k in mod_keys:
            for alg in alg_list:
                if alg in dynamic_conf[k]:
                    continue
                dynamic_conf[k].append(alg)
                should_update = True

        # enable CORS by default
        if "corsEnabled" not in dynamic_conf["corsConfigurationFilters"][0]:
            dynamic_conf["corsConfigurationFilters"][0]["corsEnabled"] = True
            should_update = True

        # add shareSubjectIdBetweenClientsWithSameSectorId
        if "shareSubjectIdBetweenClientsWithSameSectorId" not in dynamic_conf:
            dynamic_conf["shareSubjectIdBetweenClientsWithSameSectorId"] = True
            should_update = True

        # if there's no update, bail the process
        if not should_update:
            return

        logger.info("Updating oxAuth config in LDAP.")

        dynamic_conf = json.dumps(dynamic_conf)
        ox_rev = str(int(entry["oxRevision"][0]) + 1)
        ok, _ = self.backend.modify_entry(
            entry.entry_dn,
            {"oxRevision": [ox_rev], "oxAuthConfDynamic": [dynamic_conf]},
        )

        if ok:
            logger.info("Updating oxAuth config in secrets backend.")
            self.manager.secret.set("oxauth_config_base64", generate_base64_contents(dynamic_conf))

    def run_upgrade(self):
        self.modify_clients()
        self.modify_oxauth_config()
        return True
