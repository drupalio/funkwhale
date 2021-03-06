import axios from 'axios'
import logger from '@/logging'
import _ from 'lodash'

function getDefaultUrl () {
  return (
    window.location.protocol + '//' + window.location.hostname +
    (window.location.port ? ':' + window.location.port : '')
  )
}

export default {
  namespaced: true,
  state: {
    maxEvents: 200,
    frontSettings: {},
    instanceUrl: process.env.VUE_APP_INSTANCE_URL,
    events: [],
    settings: {
      instance: {
        name: {
          value: ''
        },
        short_description: {
          value: ''
        },
        long_description: {
          value: ''
        }
      },
      users: {
        registration_enabled: {
          value: true
        },
        upload_quota: {
          value: 0
        }
      },
      subsonic: {
        enabled: {
          value: true
        }
      },
      raven: {
        front_enabled: {
          value: false
        },
        front_dsn: {
          value: null
        }
      }
    }
  },
  mutations: {
    settings: (state, value) => {
      _.merge(state.settings, value)
    },
    event: (state, value) => {
      state.events.unshift(value)
      if (state.events.length > state.maxEvents) {
        state.events = state.events.slice(0, state.maxEvents)
      }
    },
    events: (state, value) => {
      state.events = value
    },
    frontSettings: (state, value) => {
      state.frontSettings = value
    },
    instanceUrl: (state, value) => {
      if (value && !value.endsWith('/')) {
        value = value + '/'
      }
      state.instanceUrl = value
      if (!value) {
        axios.defaults.baseURL = null
        return
      }
      let suffix = 'api/v1/'
      axios.defaults.baseURL = state.instanceUrl + suffix
    }
  },
  getters: {
    defaultUrl: (state) => () => {
      return getDefaultUrl()
    },
    absoluteUrl: (state) => (relativeUrl) => {
      if (relativeUrl.startsWith('http')) {
        return relativeUrl
      }
      if (state.instanceUrl.endsWith('/') && relativeUrl.startsWith('/')) {
        relativeUrl = relativeUrl.slice(1)
      }

      let instanceUrl = state.instanceUrl || getDefaultUrl()
      return instanceUrl + relativeUrl
    }
  },
  actions: {
    setUrl ({commit, dispatch}, url) {
      commit('instanceUrl', url)
      let modules = [
        'auth',
        'favorites',
        'player',
        'playlists',
        'queue',
        'radios'
      ]
      modules.forEach(m => {
        commit(`${m}/reset`, null, {root: true})
      })
    },
    // Send a request to the login URL and save the returned JWT
    fetchSettings ({commit}, payload) {
      return axios.get('instance/settings/').then(response => {
        logger.default.info('Successfully fetched instance settings')
        let sections = {}
        response.data.forEach(e => {
          sections[e.section] = {}
        })
        response.data.forEach(e => {
          sections[e.section][e.name] = e
        })
        commit('settings', sections)
        if (payload && payload.callback) {
          payload.callback()
        }
      }, response => {
        logger.default.error('Error while fetching settings', response.data)
      })
    },
    fetchFrontSettings ({commit}) {
      return axios.get('/settings.json').then(response => {
        commit('frontSettings', response.data)
      }, response => {
        logger.default.error('Error when fetching front-end configuration (or no customization available)')
      })
    }
  }
}
