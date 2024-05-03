# Kanidm

## Roles
The roles are:

name: k8s-oauth2-proxy-users
name: jellyseerr-users


name: grafana-users
name: grafana-admins

name: immich-users
name: immich-admins

name: jellyfin-users
name: jellyfin-admins

name: nextcloud-users


name: argocd-users
name: vpn-users
name: vault-admins

## LDAP connection

For login with LDAP accounts your user has to has enabled the POSIX attributes and need to set a Unix password.

## Modify ACP

Disallow displayname self modification:

```bash
cat << EOF> /tmp/modify.json
[
    { "removed": ["acp_modify_removedattr", "displayname"] },
    { "removed": ["acp_modify_presentattr", "displayname"] }
]
EOF
kanidm raw modify '{"eq": ["name", "idm_self_acp_write"]}'  /tmp/modify.json
kanidm raw search '{"eq": ["name", "idm_self_acp_write"]}'
```

## Create user

```bash
kanidm person create demo-user "demo-user" -D idm_admin
kanidm person update demo-user --mail "demo-user@example.com" -D idm_admin
kanidm person credential create-reset-token demo-user -D idm_admin

kanidm group list -D idm_admin | rg name | rg users
kanidm group add-members ${GROUP_NAME} demo-user -D idm_admin
```

## Add app to SSO

Example with jellyfin:

```bash
kanidm system oauth2 create jellyfin jellyfin  https://jellyfin.dbcloud.org/login/generic_oauth -D admin
kanidm group create jellyfin-users -D admin
kanidm group add-members jellyfin-users ${USER} -D admin
kanidm system oauth2 update-scope-map jellyfin jellyfin-users openid profile email -D admin
kanidm system oauth2 show-basic-secret jellyfin -D admin
kanidm group create jellyfin-admins -D admin
kanidm group add-members jellyfin-admins ${USER} -D admin
kanidm system oauth2 update-sup-scope-map jellyfin jellyfin-admins admin -D admin
kanidm system oauth2 prefer-short-username jellyfin -D admin
kanidm system oauth2 set-landing-url jellyfin https://jellyfin.dbcloud.org/login/generic_oauth
```

## Add app to SSO

Example with Grafana:

```bash
kanidm system oauth2 create grafana grafana  https://grafana.dbcloud.org/login/generic_oauth -D admin
kanidm group create grafana-users -D admin
kanidm group add-members grafana-users ${USER} -D admin
kanidm system oauth2 update-scope-map grafana grafana-users openid profile email -D admin
kanidm system oauth2 show-basic-secret grafana -D admin
kanidm group create grafana-admins -D admin
kanidm group add-members grafana-admins ${USER} -D admin
kanidm system oauth2 update-sup-scope-map grafana grafana-admins admin -D admin
kanidm system oauth2 prefer-short-username grafana -D admin
kanidm system oauth2 set-landing-url grafana https://grafana.dbcloud.org/login/generic_oauth
```

If PKCE needs to be disabled:

```bash
kanidm system oauth2 warning-insecure-client-disable-pkce ${CLIENT}
```
