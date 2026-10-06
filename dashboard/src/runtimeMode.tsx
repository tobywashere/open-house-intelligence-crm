import { createContext, useContext } from 'react'

export type RuntimeMode = 'standard' | 'native'
export const RuntimeModeContext = createContext<RuntimeMode>('standard')
export const useRuntimeMode = () => useContext(RuntimeModeContext)
