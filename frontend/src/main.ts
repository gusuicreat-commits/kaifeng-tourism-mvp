import { createApp } from 'vue'
import { createRouter,createWebHistory } from 'vue-router'
import App from './App.vue'
import Home from './pages/Home.vue'
import Detail from './pages/Detail.vue'
import Result from './pages/Result.vue'
import Assistant from './pages/Assistant.vue'
import Guide from './pages/Guide.vue'
import Memory from './pages/Memory.vue'
import CityTrip from './pages/CityTrip.vue'
import JourneyPage from './pages/JourneyPage.vue'
import FoodDetail from './pages/FoodDetail.vue'
import PlaceDetail from './pages/PlaceDetail.vue'
import MyPage from './pages/MyPage.vue'
import BasketPage from './pages/BasketPage.vue'
import './style.css'
import './journey.css'
import './details.css'
const router=createRouter({history:createWebHistory(),scrollBehavior:()=>({top:0}),routes:[
 {path:'/',component:Home},{path:'/guide',component:Guide},{path:'/assistant',component:Assistant},{path:'/memory',component:Memory},{path:'/scenic/:id',component:Detail},
 {path:'/city-trip/manage/:id?',component:CityTrip},
 {path:'/city-trip/:id?',component:JourneyPage},{path:'/food/:id',component:FoodDetail},{path:'/place/:id',component:PlaceDetail},{path:'/me',component:MyPage},
 {path:'/basket',component:BasketPage},{path:'/vendor/:id',component:PlaceDetail},
 {path:'/discover',redirect:'/'},{path:'/trip',redirect:'/city-trip'},
 {path:'/plan',redirect:to=>({path:'/assistant',query:to.query})},
 {path:'/crowds',redirect:to=>({path:'/assistant',query:to.query})},{path:'/result/:id',component:Result},
 {path:'/:pathMatch(.*)*',redirect:'/'}
]})
createApp(App).use(router).mount('#app')
