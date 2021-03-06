<template>
  <div class="main pusher" v-title="labels.title">
    <div class="ui vertical aligned stripe segment">
      <div v-if="isLoading" :class="['ui', {'active': isLoading}, 'inverted', 'dimmer']">
        <div class="ui text loader"><translate>Loading notifications...</translate></div>
      </div>
      <div v-else class="ui container">
        <h1 class="ui header"><translate>Your notifications</translate></h1>
        <div class="ui toggle checkbox">
          <input v-model="filters.is_read" type="checkbox">
          <label><translate>Show read notifications</translate></label>
        </div>
        <div
          v-if="filters.is_read === false && notifications.count > 0"
          @click="markAllAsRead"
          class="ui basic labeled icon right floated button">
          <i class="ui check icon" />
          <translate>Mark all as read</translate>
        </div>
        <div class="ui hidden divider" />
        <table v-if="notifications.count > 0" class="ui table">
          <tbody>
            <notification-row :item="item" v-for="item in notifications.results" :key="item.id" />
          </tbody>
        </table>
        <p v-else>
          <translate>We don't have any notification to display!</translate>
        </p>
      </div>
    </div>
  </div>
</template>

<script>
import {mapState} from 'vuex'
import axios from 'axios'
import logger from '@/logging'

import NotificationRow from '@/components/notifications/NotificationRow'

export default {
  data () {
    return {
      isLoading: false,
      notifications: null,
      filters: {
        is_read: false
      }
    }
  },
  components: {
    NotificationRow
  },
  created () {
    this.fetch(this.filters)
    this.$store.commit('ui/addWebsocketEventHandler', {
      eventName: 'inbox.item_added',
      id: 'notificationPage',
      handler: this.handleNewNotification
    })
  },
  destroyed () {
    this.$store.commit('ui/removeWebsocketEventHandler', {
      eventName: 'inbox.item_added',
      id: 'notificationPage',
    })
  },
  computed: {
    ...mapState({
      events: state => state.instance.events
    }),
    labels () {
      return {
        title: this.$gettext('Notifications'),
      }
    }
  },
  methods: {
    handleNewNotification (event) {
      this.notifications.results.unshift(event.item)
    },
    fetch (params) {
      this.isLoading = true
      let self = this
      axios.get('federation/inbox/', {params: params}).then((response) => {
        self.isLoading = false
        self.notifications = response.data
      })
    },
    markAllAsRead () {
      let self = this
      let before = this.notifications.results[0].id
      let payload = {
        action: 'read',
        objects: 'all',
        filters: {
          is_read: false,
          before
        }
      }
      axios.post('federation/inbox/action/', payload).then((response) => {
        self.$store.commit('ui/notifications', {type: 'inbox', count: 0})
        self.notifications.results.forEach(n => {
          n.is_read = true
        })

      })
    },
  },
  watch: {
    'filters.is_read' () {
      this.fetch(this.filters)
    }
  }
}
</script>

<!-- Add "scoped" attribute to limit CSS to this component only -->
<style>
.event .ui.label.avatar {
  font-size: 1.5em;
  position: relative;
  top: 0.35em;
}
</style>
