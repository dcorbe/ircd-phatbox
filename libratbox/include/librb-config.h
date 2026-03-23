/*
 * librb-config.h: libratbox config file. Please modify configure.ac
 */

#ifndef __LIBRB_CONFIG_H
#define __LIBRB_CONFIG_H

#define RB_HAVE_STRDUP 1
#define RB_HAVE_STRNDUP 1
#define RB_HAVE_STRLCPY 1
#define RB_HAVE_STRLCAT 1
#define RB_HAVE_STRNLEN 1
#define RB_IPV6 1
#define RB_SIZEOF_TIME_T 8
#define RB_SIZEOF_LONG 8
#define RB_HAVE_ALLOCA_H 1
#include <string.h>
#include <stdbool.h>
#include <stdint.h>
#include <inttypes.h>
#include <sys/types.h>
#include <sys/time.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <errno.h>
typedef socklen_t rb_socklen_t;
#define RB_SOCKADDR_HAS_SA_LEN 1
#define rb_sockaddr_storage sockaddr_storage
#endif /* __LIBRB_CONFIG_H */
